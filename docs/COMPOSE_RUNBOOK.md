# Compose Runbook

This runbook documents developer bootstrap and day-2 operations for the current Docker Compose stack in this repository.

References:
- `AGENTS.md`

Current shell note:
- The local web shell is currently unauthenticated.
- No Auth0, hosted login, or SSO bootstrap is required to access `http://localhost:3000`.

## Stack Services

- `postgres` (`5432`)
- `redis` (`6379`)
- `mlflow` (`5001`)
- `litellm-proxy` (`4000`)
- `backend` (`8000`)
- `worker` (no host port)
- `web` (`3000`)

Named volumes:
- `postgres_data` for PostgreSQL data
- `mlflow_artifacts` for MLflow artifacts (MLflow metadata is stored in the Postgres `mlflow` schema)
- `bundles_data` shared between `backend` and `worker` at `/tmp/dspy-trainer/bundles` for uploaded module bundles
- `optimization_artifacts_data` shared between `backend` and `worker` at `/tmp/dspy-trainer/optimization_artifacts` so succeeded optimization artifacts can be materialized into new bundles

MLflow concurrency can be tuned with `MLFLOW_WEB_WORKERS` in `.env` (default `4`).

## Developer Bootstrap

Before starting the stack, ensure `.env` contains `GITHUB_PAT` if you want to import, sync, or push GitHub-backed bundles. Backend and worker read that variable server-side; the web UI only reports whether GitHub access is configured. GitHub imports may target either the repo root or a configured bundle subfolder. Optimization writeback now pushes to an `optimization-<job-prefix>` branch for manual merge, so also set `GIT_COMMIT_NAME` and `GIT_COMMIT_EMAIL` (defaults are provided if omitted).

LiteLLM setup note:
- The repo does not ship a default upstream model configuration.
- Normal operator flow is to start the proxy, then create LM Profiles in the app UI.
- Those LM Profiles provision model routing and virtual keys dynamically inside LiteLLM.

Bundle runtime note:
- If a tracked bundle contains `requirements.txt`, backend and worker install those dependencies automatically before executing the bundle.

Run from repository root:

```bash
docker compose pull --ignore-pull-failures
docker compose build --pull
docker compose up -d --remove-orphans
```

Expected outcome:
- Images are present and build completes.
- Containers start in detached mode.
- `backend`, `worker`, and `web` wait on upstream health checks.

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
docker compose logs -f --timestamps backend worker litellm-proxy
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
curl -fsS -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-sk-local-dev-master-key}" http://localhost:4000/health
```

### Backend Dependency Checks from Container

```bash
docker compose exec -T backend python -c "import os, redis; redis.Redis.from_url(os.environ['DSPY_TRAINER_REDIS_URL']).ping(); print('redis ok')"
docker compose exec -T backend python -c "import os, urllib.request; urllib.request.urlopen(os.environ['DSPY_TRAINER_MLFLOW_TRACKING_URI'], timeout=5); print('mlflow ok')"
docker compose exec -T backend python -c "import os, urllib.request; req=urllib.request.Request(os.environ['DSPY_TRAINER_LITELLM_BASE_URL'] + '/health', headers={'Authorization':'Bearer ' + os.environ['DSPY_TRAINER_LITELLM_API_KEY']}); urllib.request.urlopen(req, timeout=5); print('litellm ok')"
docker compose exec -T backend python -c "import os; print('github ok' if (os.environ.get('DSPY_TRAINER_GITHUB_PAT') or os.environ.get('GITHUB_PAT')) else 'github missing')"
docker compose exec -T backend python -c "import os; print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_NAME', '')); print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_EMAIL', ''))"
```

## Troubleshooting

### LiteLLM Auth Failures (`401` or `403`)

Symptoms:
- `backend` or `worker` logs contain unauthorized responses from LiteLLM.

Checks:

```bash
docker compose exec -T backend python -c "import os; print(os.environ['DSPY_TRAINER_LITELLM_API_KEY'])"
docker compose exec -T litellm-proxy python -c "import os; print(os.environ['LITELLM_MASTER_KEY'])"
```

Remediation:
- Ensure both values match (`DSPY_TRAINER_LITELLM_API_KEY` and `LITELLM_MASTER_KEY`).
- If you changed `.env`, restart services:

```bash
docker compose up -d --force-recreate litellm-proxy backend worker
```

### LiteLLM Endpoint/Model Issues (`404`, `422`, provider errors)

Symptoms:
- Proxy healthy but completion calls fail.
- Logs mention missing model, invalid provider config, or Azure base/version errors.

Checks:

```bash
docker compose logs --tail=200 litellm-proxy
```

Remediation:
- Confirm the proxy is healthy and the target LM Profile was created successfully in the app.
- If model creation or key provisioning failed, inspect backend logs and retry LM Profile creation.
- Recreate proxy after env/config changes:

```bash
docker compose up -d --force-recreate litellm-proxy
```

### Backend Not Ready

Symptoms:
- `curl -fsS http://localhost:8000/ready` fails.
- `docker compose ps` shows `backend` as `starting` or `unhealthy`.

Checks:

```bash
docker compose logs --tail=200 backend
docker compose ps postgres redis mlflow litellm-proxy backend
```

Remediation sequence:
1. Confirm upstream services are healthy (`postgres`, `redis`, `mlflow`, `litellm-proxy`).
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
