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
- `deployer` (internal only; the sole Docker socket/SDK holder)
- `web`
- `caddy`

Named volumes:
- `postgres_data` for PostgreSQL data
- `mlflow_artifacts` for MLflow artifacts (MLflow metadata is stored in the Postgres `mlflow` schema)
- `bundles_data` shared between `backend` and `worker` at `/tmp/dspy-trainer/bundles` for uploaded module bundles
- `checkouts_data` shared read-only with `deployer` so validated revision snapshots can be baked
- `optimization_artifacts_data` shared between `backend` and `worker` at `/tmp/dspy-trainer/optimization_artifacts` so succeeded optimization artifacts can be materialized into new bundles

MLflow concurrency can be tuned with `MLFLOW_WEB_WORKERS` in `.env` (default `4`). Compose builds a small project MLflow image that adds the PostgreSQL driver required by both the schema bootstrap and MLflow's backend store. MLflow serves internally at root: leave `MLFLOW_STATIC_PREFIX` empty so both its REST API and `/v1/traces` span ingestion remain reachable. Caddy strips the browser-facing `/mlflow` prefix and forwards requests to that root service. Set `MLFLOW_CORS_ALLOWED_ORIGINS` to every browser origin that serves this UI (for example `http://agents.abatix.com:8080` in a remote deployment). Also set `MLFLOW_ALLOWED_HOSTS_EXTRA` to the public host and port (for example `agents.abatix.com:8080`) so MLflow can preserve the browser Host header for same-origin UI APIs. Compose also forwards `CADDY_HTTP_PORT` into the MLflow container so its allowed-hosts list stays aligned if you move the proxy off the default `8080` port. Compose reads worker replica counts from `.env` via `DSPY_TRAINER_TOTAL_WORKERS` and `DSPY_TRAINER_TOTAL_ENDPOINT_WORKER_REPLICAS`, and it forwards `DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS` when operators need to tune endpoint-worker stale detection.

## Developer Bootstrap

Before starting the stack, ensure `.env` contains `GITHUB_PAT` if you want to import, sync, or push GitHub-backed bundles. Backend and worker read that variable server-side; the web UI only reports whether GitHub access is configured. GitHub imports may target either the repo root or a configured bundle subfolder. Optimization writeback now pushes to an `optimization-<job-prefix>` branch for manual merge, so also set `GIT_COMMIT_NAME` and `GIT_COMMIT_EMAIL` (defaults are provided if omitted).

The default local `.env.sample` also defines deployer leader/claim timeouts, poll interval, bounded log size, immutable backend image ID, and stable Compose project/network/deployment identities. All deployer durations and sizes must be positive; the log cap cannot exceed 262144 bytes. `DSPY_TRAINER_DEPLOYER_BACKEND_BASE_IMAGE_ID` must be the exact local `sha256:...` image ID, never a mutable tag.

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
- If a tracked bundle contains `requirements.txt`, backend, general workers, and endpoint workers install those dependencies automatically before executing the bundle.
- Bundle system dependency commands and Python requirements installation share PostgreSQL advisory-lock slots across `backend`, `worker`, and `endpoint-worker`. `DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY` must be positive and defaults to `8`.
- Endpoint workers continue sending `preparing` heartbeats while waiting for a slot and while installing dependencies.

Build the backend first, record its immutable local image ID in `.env`, then start the stack. The deployer Dockerfile and every generated revision image use that exact ID as their base:

```bash
docker compose build --pull backend
docker image inspect --format '{{.Id}}' "${DSPY_TRAINER_BACKEND_IMAGE:-dspy-trainer-backend:local}"
# Set DSPY_TRAINER_DEPLOYER_BACKEND_BASE_IMAGE_ID to the printed sha256 ID.
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
docker compose logs -f --timestamps backend worker deployer
```

### Rebuild

Rebuild the backend, update `DSPY_TRAINER_DEPLOYER_BACKEND_BASE_IMAGE_ID` to its new immutable ID, then rebuild and restart all images:

```bash
docker compose build --pull backend
docker image inspect --format '{{.Id}}' "${DSPY_TRAINER_BACKEND_IMAGE:-dspy-trainer-backend:local}"
# Update .env with the printed ID before continuing.
docker compose build --pull
docker compose up -d --remove-orphans
```

Recreate the Python runtime and deployer services only after the immutable ID is current:

```bash
docker compose build --pull backend worker endpoint-worker deployer
docker compose up -d --force-recreate backend worker endpoint-worker deployer
```

## Revision Image Builder Contract

The revision-image builder is a library seam for the dedicated deployer; it does not poll queues, elect a leader, start services, create endpoint containers, or run Compose. Callers supply one immutable snapshot directory, its canonical `sha256:` content digest, build/module/revision identity, stack owner (the Compose project name), platform version, image repository, and an immutable local backend image ID. The Docker SDK adapter submits one standard Engine build with cache enabled, `pull=false`, no platform override, and no build arguments, then inspects the returned image ID. Missing or mismatched provenance labels make the result failed and leave no ready image ID.

The generated context contains only `Dockerfile`, `dspy-trainer-endpoint-worker`, and the selected source under `bundle/`. Tar ordering and metadata are normalized. `.git` and tool caches are always omitted; `.env*`, key material, and credential files are omitted unless an exact in-root file is declared by `bundle.toml` under `[image_build] include_files`. Escaping or absolute symlinks, special files, directory overrides, digest mismatches, and attempts to restore control directories are rejected before Docker is called.

The generated Dockerfile uses the supplied immutable backend image ID directly in `FROM`, applies the complete `io.dspy-trainer.*` label set, copies source to `/opt/dspy-bundle`, executes each `runtime.system_dependency_commands` entry in order, installs `requirements.txt` afterward when present, captures sorted `pip freeze --all` output at `/opt/dspy-trainer/python-manifest.txt`, and installs the baked endpoint-worker entrypoint. Tags are `<repository>:<normalized-revision>-<24-character-build-digest-prefix>`; build ID and generation are digest inputs, so generations cannot reuse a tag.

## Durable Revision Image Coordinator

The dedicated `deployer` opens one PostgreSQL session and must acquire the global advisory leader lock before queue work. Claiming uses `FOR UPDATE SKIP LOCKED`, but a second durable guard refuses a claim while any non-expired row remains `building`; therefore only one image build can run globally. Endpoint-serving module revisions sort ahead of other eligible revisions, then `available_at`, `queued_at`, and build ID provide deterministic FIFO ordering. Eligibility requires the current revision to be synced, not deleted, validated as `passed` for that exact revision, and backed by a snapshot path and digest.

Claim owner plus incrementing attempt is the completion fencing token. The leader renews the existing claim expiry while building and stores bounded log chunks. A successor requeues expired claims. PostgreSQL rechecks the complete eligibility predicate in both the atomic claim and completion statements; a ready result whose revision was revoked is committed as failed without an image reference, even if it finishes before the monitor's next poll. Every synchronous Docker SDK build runs in a dedicated deployer-owned child process. If eligibility changes, a retry/rebuild supersedes an active generation, or shutdown begins, the coordinator terminates that child; operating-system process teardown closes its Docker HTTP socket without cross-thread generator access. Ownership and eligibility fencing reject late publication. A failed replacement does not mutate an earlier ready generation. Retry and rebuild-all create new generations and retain retry history.

Only `deployer` installs the Docker SDK and mounts `/var/run/docker.sock`; it has no published port and receives no GitHub, LM, provider, or module runtime secrets. Backend, web, general workers, and endpoint workers receive neither the SDK nor socket. The advisory lock is visible in `pg_locks`, the deployer session is named `dspy-trainer-deployer:<instance>` in `pg_stat_activity`, and an active build's renewed claim expiry is its durable liveness lease.

### Coordinator Deployment-host Acceptance (Do Not Run on Development Workstations)

1. Start two deployer processes for the same deployment and confirm exactly one matching advisory lock holder in PostgreSQL and at most one `building` row.
2. Queue equal-priority fixtures and a fixture belonging to a module with a serving endpoint; confirm the serving revision starts first and the remainder start in `available_at, queued_at, id` order.
3. Kill the leader during a deliberately slow build. After claim expiry, confirm the successor requeues and completes that same generation, with attempt incremented and no permanently `building` row.
4. Supersede a queued fixture and then an active slow fixture. Confirm the queued row becomes terminal, the deployer terminates the active build child, Docker daemon-side work stops rather than continuing after client disconnect, the new generation runs, and the old result cannot publish.
5. Stop the leader cleanly during a slow build and confirm the claim returns to `queued`; make Docker unavailable for another build and confirm only that generation becomes `failed` with bounded logs while any prior ready generation remains ready.
6. Inspect every Compose service and image package set: only `deployer` has the socket and Docker SDK, and the deployer has no published port or runtime-secret environment variables.
### Deployment-host Acceptance (Do Not Run on Development Workstations)

On the deployment host, use a harmless validated fixture snapshot containing one observable system command and one small Python requirement. Calculate its digest with `calculate_source_content_digest`, build it through `RevisionImageBuilder(DockerSdkImageAdapter.from_env())`, and retain the returned tag, image ID, labels, bounded log, and manifest digest. Then:

1. Confirm the result is `ready`, the inspected ID equals the returned immutable ID, and every required `io.dspy-trainer.*` label exactly matches the supplied build identity.
2. Inspect `/opt/dspy-bundle` and `/opt/dspy-trainer/python-manifest.txt` in the built image to confirm all non-excluded fixture assets, the system dependency, and the Python requirement are baked.
3. Search the context fixture, image config, labels, and `docker image history --no-trunc` output for sentinel provider-key, GitHub-token, module-environment, and host-environment values; none may occur.
4. Repeat with the same revision and a new build ID/generation; confirm the tag and image ID are distinct and the first tag still resolves to its original ID.
5. Run failing system-command and requirements fixtures; confirm each result is `failed`, keeps bounded useful logs, and returns no ready image ID.
6. Confirm the daemon was not contacted for an escaping-symlink or source-digest-mismatch fixture, and confirm no registry login, pull, or push occurs.

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
docker compose exec -T backend python -c "import os; print(os.environ['DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY'])"
docker compose exec -T worker python -c "import os; print(os.environ['DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY'])"
docker compose exec -T endpoint-worker python -c "import os; print(os.environ['DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY'])"
docker compose exec -T backend python -c "import os; print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_NAME', '')); print(os.environ.get('DSPY_TRAINER_GIT_COMMIT_EMAIL', ''))"
```

## Revision Image Build Operations

Run these commands on the deployment host after the backend and deployer are healthy. They only use the backend HTTP API; operators do not need direct database or Docker access.

```bash
# List queued/active/failed/ready generations.
curl -fsS 'http://localhost:8000/revision-image-builds?limit=50&offset=0'

# Read status and bounded retained logs.
curl -fsS 'http://localhost:8000/revision-image-builds/BUILD_ID'
curl -fsS 'http://localhost:8000/revision-image-builds/BUILD_ID/logs?offset=0&limit=16384'

# Queue a new generation for one eligible failed or ready build.
curl -fsS -X POST 'http://localhost:8000/revision-image-builds/BUILD_ID/retry'

# Queue one generation for every current eligible revision.
curl -fsS -X POST 'http://localhost:8000/revision-image-builds/rebuild-all'
```

A `409` response with code `build_conflict` means an equivalent generation is already active or the requested build cannot be retried. `not_eligible` means the revision is no longer the current validated, synced source. A `503` with code `build_coordinator_unavailable` means the backend lacks the deployer identity/base-image configuration. Source validation and sync are independent of these build failures.

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
