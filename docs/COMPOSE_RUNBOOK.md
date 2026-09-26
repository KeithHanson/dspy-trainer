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

The default local .env.sample defines deployer leader/claim timeouts, endpoint readiness/drain/reconcile timing, per-module image retention, bounded log size, a stable explicitly tagged local backend image name, a strict managed-label namespace, and stable Compose project/network/deployment identities. All deployer durations and sizes must be positive; image retention cannot be lower than two and the log cap cannot exceed 262144 bytes. The named image is discovery input only: startup inspects it once, persists the immutable sha256 ID, and generated builds receive only that ID. A later tag change is rejected until an operator explicitly accepts it with no queued or running builds. DSPY_TRAINER_COMPOSE_NETWORK_PROJECT_LABEL must equal the actual com.docker.compose.project label on the selected runtime network.

Secret storage note:
- `DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY` is required if you want to store module environment entries or LM Profile provider API keys in Postgres.
- Generate it once and keep it stable across restarts.

LM Profile setup note:
- The repo does not ship a default upstream model configuration.
- Normal operator flow is to start the stack, then create LM Profiles in the app UI.
- Each LM Profile stores the direct provider endpoint, model identifier, optional LM class override, and optional provider API key.

Managed endpoint worker note:
- The Compose `endpoint-worker` service is the migration-only static pool. It can serve only deployments whose durable `legacy_fallback` flag remains true. Existing legacy endpoints retain that fallback throughout managed-image warming and failed replacement attempts; new endpoints never receive it.
- A revision-image worker starts in `managed_image` mode with immutable endpoint, worker, deployment, slot, rollout-generation, build, revision, and `/opt/dspy-bundle` identity. Registration also requires an exact non-removed managed-container row for that slot; stale or mismatched identities are rejected.
- Managed readiness requires the exact worker/deployment/slot/generation plus matching desired and warmed build/revision heartbeats. Managed jobs use endpoint/build/revision-specific Redis queues and carry the same provenance into the MLflow trace.

Bundle runtime note:
- Backend, general workers, and explicit `legacy_static` endpoint workers retain checkout-based dependency installation for migration compatibility.
- Managed revision-image workers never resolve a checkout or install dependencies at startup or invocation time; source, system packages, and Python requirements are baked during image construction.
- The generated managed entrypoint clears the inherited environment and passes only process basics, Postgres, Redis, MLflow, endpoint/worker/build/revision identity, queue settings, and the module-environment encryption key. Git, GitHub, deployer/build, and checkout configuration are not passed.

Build the named backend image before the deployer image. On first startup the deployer preflight inspects the local name, records its immutable ID, and refuses to pull a substitute:

~~~bash
docker compose build --pull backend
docker compose build --pull
docker compose up -d --scale deployer=2 --remove-orphans
for id in $(docker compose ps -q deployer); do
  docker exec "$id" python backend/deployer.py --liveness
  docker exec "$id" python backend/deployer.py --readiness
done
~~~

## Non-Interactive Operations

### Startup

```bash
docker compose up -d --scale deployer=2 --remove-orphans
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

Rebuilds deliberately stop the deployer before changing the named backend image. Confirm the build-status API has no queued or building generation, then accept the newly inspected ID explicitly:

~~~bash
curl -fsS 'http://localhost:8000/revision-image-builds?limit=100&offset=0'
docker compose stop deployer
docker compose build --pull backend deployer
docker compose run --rm deployer python backend/deployer.py --preflight --accept-base-image
docker compose build --pull worker endpoint-worker
docker compose up -d --scale deployer=2 --force-recreate backend worker endpoint-worker deployer
for id in $(docker compose ps -q deployer); do
  docker exec "$id" python backend/deployer.py --liveness
  docker exec "$id" python backend/deployer.py --readiness
done
~~~

Never accept base-image drift while a build is active. Existing build rows retain their original immutable base ID; only generations queued after acceptance use the new ID. To roll back, stop the deployer, restore the prior backend image under the configured local name, rebuild the deployer image, repeat the explicit acceptance command, recreate services, and use rebuild-all only after readiness is healthy.

## Revision Image Builder Contract

The revision-image builder is a library seam for the dedicated deployer; it does not poll queues, elect a leader, start services, create endpoint containers, or run Compose. Callers supply one immutable snapshot directory, its canonical `sha256:` content digest, build/module/revision identity, stack owner (the Compose project name), platform version, image repository, and an immutable local backend image ID. The Docker SDK adapter submits one standard Engine build with cache enabled, `pull=false`, no platform override, and no build arguments, then inspects the returned image ID. Missing or mismatched provenance labels make the result failed and leave no ready image ID.

The generated context contains only `Dockerfile`, `dspy-trainer-endpoint-worker`, and the selected source under `bundle/`. Tar ordering and metadata are normalized. `.git` and tool caches are always omitted; `.env*`, key material, and credential files are omitted unless an exact in-root file is declared by `bundle.toml` under `[image_build] include_files`. Escaping or absolute symlinks, special files, directory overrides, digest mismatches, and attempts to restore control directories are rejected before Docker is called.

The generated Dockerfile uses the supplied immutable backend image ID directly in `FROM`, applies the complete `io.dspy-trainer.*` label set, copies source to `/opt/dspy-bundle`, executes each `runtime.system_dependency_commands` entry in order, installs `requirements.txt` afterward when present, captures sorted `pip freeze --all` output at `/opt/dspy-trainer/python-manifest.txt`, and installs the baked endpoint-worker entrypoint. Tags are `<repository>:<normalized-revision>-<24-character-build-digest-prefix>`; build ID and generation are digest inputs, so generations cannot reuse a tag.

## Managed Endpoint Image Execution

Managed invocation routing chooses only live `listening` workers whose endpoint, execution mode, desired/warmed build, desired/warmed revision, baked bundle path, deployment ID, slot, and rollout generation match the current deployment. During warming, invocations continue on the active managed revision, or on the legacy pool while `legacy_fallback` is true. Target workers receive no traffic until every exact desired slot is ready and the reconciler atomically promotes target to active. Each queued job repeats the accepted build/revision/path, and the worker rejects a mismatch before invoking bundle code. MLflow trace attributes record `revision_image_build_id`, `bundle_revision_id`, and `execution_mode` from the worker's accepted assignment.

### Managed Execution Deployment-host Acceptance (Do Not Run on Development Workstations)

1. Choose an endpoint whose deployment has a ready active revision-image build. Start its managed worker with only Postgres, Redis, MLflow, exact endpoint/worker/deployment/slot/rollout-generation identity, the baked build/revision identity, and the module-environment encryption key when encrypted runtime secrets are configured.
2. Inspect the worker container mounts and environment. Confirm no checkout or bundle volume is mounted; `/opt/dspy-bundle` comes from the image; and no GitHub, Git, Docker, deployer/build, or broad host environment is present.
3. Capture startup logs and process activity. Confirm the worker performs no `apt`, `pip`, requirements installation, Git operation, or mutable checkout resolution before reporting matching desired/warmed build and revision readiness.
4. Invoke the endpoint once through the synchronous API and once through SSE. Confirm both jobs use the endpoint/build/revision-specific Redis queue and their MLflow traces report the active build and revision.
5. Sync or modify the host checkout after the image worker is ready, then invoke both paths again. Confirm responses and trace provenance remain pinned to the baked revision rather than observing the newer checkout.
6. Start a worker with a wrong endpoint, worker, deployment, slot, rollout generation, build, revision, or bundle path and confirm registration/readiness fails and it never consumes endpoint traffic. Confirm a static worker can serve only an endpoint whose current deployment retains `legacy_fallback: true`.

## Durable Revision Image Coordinator

Each deployer replica opens independent PostgreSQL sessions. The revision-image build coordinator and endpoint reconciler acquire different global advisory locks, so their role holders may be different replicas. Build claiming uses FOR UPDATE SKIP LOCKED, but a second durable guard refuses a claim while any non-expired row remains building; therefore only one image build can run globally. Endpoint-serving module revisions sort ahead of other eligible revisions, then available_at, queued_at, and build ID provide deterministic FIFO ordering. Eligibility requires the current revision to be synchronized and validated.

Claim owner plus incrementing attempt is the completion fencing token. The leader renews the existing claim expiry while building and stores bounded log chunks. A successor requeues expired claims. PostgreSQL rechecks the complete eligibility predicate in both the atomic claim and completion statements; a ready result whose revision was revoked is committed as failed without an image reference, even if it finishes before the monitor's next poll. Every synchronous Docker SDK build runs in a dedicated deployer-owned child process. If eligibility changes, a retry/rebuild supersedes an active generation, or shutdown begins, the coordinator terminates that child; operating-system process teardown closes its Docker HTTP socket without cross-thread generator access. Ownership and eligibility fencing reject late publication. A failed replacement does not mutate an earlier ready generation. Retry and rebuild-all create new generations and retain retry history.

Only deployer installs the Docker SDK and mounts /var/run/docker.sock; it has no published port and receives no GitHub, LM, provider, or module runtime secrets. Backend, web, general workers, and endpoint workers receive neither the SDK nor socket. Advisory locks are visible in pg_locks, and deployer sessions are named dspy-trainer-deployer:<instance> in pg_stat_activity. Each process start has a unique instance identity and publishes separate durable build and endpoint coordinator heartbeat rows. Followers update only their own rows, stale rows remain visible for failover diagnosis, and an active build's renewed claim expiry is its durable liveness lease.

### Coordinator Deployment-host Acceptance (Do Not Run on Development Workstations)

1. Start the stack with docker compose up -d --scale deployer=2; do not assign fixed container names. Inspect pg_locks and sanitized deployer_coordinator_heartbeats rows. Confirm exactly one fresh leader for each coordinator, allowing build and endpoint roles to reside on different replicas, and at most one building row.
2. Poll liveness and readiness from both replica container IDs. Confirm follower heartbeats do not erase either leader and readiness remains stable while exactly one fresh leader exists for each role.
3. Kill the replica holding the build role during a deliberately slow build. Observe the old row become stale, exactly one successor acquire the build role, readiness recover without ever reporting multiple fresh build leaders, and the expired claim requeue and complete with its attempt incremented.
4. Kill the replica holding the endpoint role. Observe the same zero-to-one failover, retained stale row, and restored readiness without a duplicate fresh endpoint leader.
5. Queue equal-priority fixtures and a fixture belonging to a module with a serving endpoint; confirm the serving revision starts first and the remainder start in available_at, queued_at, id order.
6. Supersede a queued fixture and then an active slow fixture. Confirm the queued row becomes terminal, the deployer terminates the active build child, Docker daemon-side work stops rather than continuing after client disconnect, the new generation runs, and the old result cannot publish.
7. Stop a build role holder cleanly during a slow build and confirm the claim returns to queued; make Docker unavailable for another build and confirm only that generation becomes failed with bounded logs while any prior ready generation remains ready.
8. Inspect every Compose service and image package set: only deployer has the socket and Docker SDK, and deployer replicas have no published port or runtime-secret environment variables.

## Managed Endpoint Container Reconciliation

The independently elected endpoint coordinator reconciles durable endpoint deployment intent against Docker one action at a time. It discovers the configured network by exact name and Compose project label, starts deterministic slots with restart policy unless-stopped, and always uses the inspected immutable image ID. Adoption and deletion require the complete io.dspy-trainer.* container identity: platform owner, stack owner, managed kind, endpoint, deployment, slot, build, revision, rollout generation, worker, and image ID.

Rolling replacement is slot-by-slot. A target container must register live in `managed_image` mode with the exact endpoint, deployment, slot, rollout generation, worker, desired/warmed build, and desired/warmed revision before its old slot can drain. The active revision or legacy fallback remains routable until every target slot is ready; promotion and legacy-fallback removal occur in one database update. Draining atomically clears assignment before the worker may claim another job. A job already claimed may finish; after the drain timeout the reconciler force-removes the container and records the timeout, reason, and bounded final log. Startup or readiness failure enters durable rollback and restores any missing old-revision slots before target containers are removed.

Restarting the deployer re-observes only containers whose deployment ID and rollout generation match the current intent, then resumes the durable phase without duplicating slots. Pinned worker-count changes update the latest managed deployment and scale from that durable intent. Scale-down retires the highest surplus slots deterministically. Endpoint deletion first tombstones the API row and blocks worker claims; the reconciler drains and removes exact owned containers before deleting their RESTRICT-protected container/deployment rows and finalizing the endpoint. Cleanup runs only after all deployment intents converge. It retains at least the newest two ready images per module and never prunes an image referenced as active, target, previous, or by a non-removed managed container; a successful prune marks the build `pruned` durably. Restarting the backend preserves registry rows for diagnostics and continuity, marks only expired heartbeat/runtime identities stale, and accepts fresh exact registrations from surviving managed containers.

Container absence is durable evidence only after a complete, successful Docker listing in which every returned container was inspectable. If an exact owned container recorded in PostgreSQL is absent from that complete observation, the reconciler finalizes its container row as removed; this lets a tombstoned endpoint finish deletion and releases the row's image reference for retention pruning. A partial, failed, or uninspectable Docker observation preserves container rows, endpoint tombstones, and image references for the next cycle. Similarly named or incompletely labeled foreign resources are never adopted, stopped, or removed.

### Reconciler Deployment-host Acceptance (Do Not Run on Development Workstations)

1. Build the configured local backend image, start the deployer, and confirm --preflight reports the inspected immutable ID plus exactly one configured network/project-label match. Retag the name and confirm normal startup rejects drift; after all builds drain, confirm --accept-base-image records the new ID.
2. Create a ready managed deployment pinned above one replica. Confirm every slot has a deterministic distinct name, exact immutable image ID, `unless-stopped`, the complete ownership label set, and only the intended network and runtime environment.
3. Keep one invocation active while deploying a new ready revision. Observe mixed old/new exact build labels during rollout, verify each new slot becomes registry-ready before its old peer starts draining, and confirm the active invocation completes without interruption.
4. Deploy an image that starts but never reports matching readiness. After the readiness timeout, confirm rollback records the bounded reason, restores all old slots, and removes only target containers.
5. Restart the deployer while target startup, readiness wait, and drain are each in progress. Confirm reconciliation resumes without duplicate slots, duplicate claims, or removal of a ready old slot before its replacement.
6. Create similarly named containers and images with missing labels, a different owner/project, or a wrong immutable image ID. Exercise rollout, scale-down, and deletion; confirm every foreign resource remains untouched.
7. Scale the endpoint up and down, then delete its deployment intent. Confirm deterministic slot creation and highest-slot retirement, readiness-before-drain, active-drain timeout behavior, and complete removal of only owned endpoint containers.
8. Produce more than the configured retention count of ready images. Confirm current, previous, target, and container-referenced images remain; only old unreferenced images are deleted and their build rows become `pruned`.

### Builder Deployment-host Acceptance (Do Not Run on Development Workstations)

On the deployment host, use a harmless validated fixture snapshot containing one observable system command and one small Python requirement. Calculate its digest with `calculate_source_content_digest`, build it through `RevisionImageBuilder(DockerSdkImageAdapter.from_env())`, and retain the returned tag, image ID, labels, bounded log, and manifest digest. Then:

1. Confirm the result is `ready`, the inspected ID equals the returned immutable ID, and every required `io.dspy-trainer.*` label exactly matches the supplied build identity.
2. Inspect `/opt/dspy-bundle` and `/opt/dspy-trainer/python-manifest.txt` in the built image to confirm all non-excluded fixture assets, the system dependency, and the Python requirement are baked.
3. Search the context fixture, image config, labels, and `docker image history --no-trunc` output for sentinel provider-key, GitHub-token, module-environment, and host-environment values; none may occur.
4. Repeat with the same revision and a new build ID/generation; confirm the tag and image ID are distinct and the first tag still resolves to its original ID.
5. Run failing system-command and requirements fixtures; confirm each result is `failed`, keeps bounded useful logs, and returns no ready image ID.
6. Confirm the daemon was not contacted for an escaping-symlink or source-digest-mismatch fixture, and confirm no registry login, pull, or push occurs.

## Deployer Preflight, Migration, and Evidence

The deployer is local-only: it publishes no port, is the only Compose service with the Docker SDK and socket mount, and receives neither GitHub/provider credentials nor general module secrets. Its readiness command verifies Docker access, the named base image and persisted immutable ID, the exact Compose network/project label, database migrations, label namespace, and a single repeatable-read snapshot containing exactly one fresh leader for each independent coordinator. Zero or multiple fresh leaders for either role fail closed; diagnostics report fresh and stale counts separately. Its liveness command checks only that replica's process heartbeat.

The migration copies immutable base-image identity from the legacy singleton state into deployer_base_image_state before removing the old table. Leadership is not migrated as fresh state. Every process start writes separate rows keyed by deployment, unique instance identity, and coordinator role in deployer_coordinator_heartbeats; stale rows are retained so restart and failover history cannot overwrite a current role holder.

~~~bash
for id in $(docker compose ps -q deployer); do
  docker exec "$id" python backend/deployer.py --liveness
  docker exec "$id" python backend/deployer.py --readiness
done
~~~

For coexistence backfill, keep the static endpoint-worker pool running. Use only the existing status/operator APIs: call rebuild-all once, poll revision-image-builds until every required current revision is ready, then inspect each endpoint deployment. The static pool remains eligible only where legacy_fallback is true; managed endpoint/build/revision queues are isolated from the general and static queues. A safe cutover is reported as migration state managed, phase ready, no target rollout, and every desired managed slot ready.

Rollback is durable and automatic for startup/readiness/drain failures: inspect the deployment rollback reason and bounded build logs, fix or rebuild the target, and let reconciliation restore the previous managed revision or retained legacy fallback. Image retention keeps at least the configured newest ready images per module and never prunes active, target, previous, or container-referenced images.

Capture only sanitized, bounded evidence. The preflight JSON intentionally omits DSNs, credentials, socket paths, and raw exceptions. Do not attach container inspect output, environment dumps, Docker socket metadata, or unbounded logs.

~~~bash
umask 077
mkdir -p deployment-evidence
: > deployment-evidence/deployer-health.jsonl
for id in $(docker compose ps -q deployer); do
  docker exec "$id" python backend/deployer.py --liveness >> deployment-evidence/deployer-health.jsonl
  docker exec "$id" python backend/deployer.py --readiness >> deployment-evidence/deployer-health.jsonl
done
curl -fsS 'http://localhost:8000/revision-image-builds?limit=100&offset=0' > deployment-evidence/revision-image-builds.json
curl -fsS 'http://localhost:8000/bundle-endpoints/ENDPOINT_ID/deployment' > deployment-evidence/endpoint-deployment.json
docker compose ps > deployment-evidence/compose-ps.txt
~~~
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

## Managed Endpoint Deployment Operations

Create returns `409` before any endpoint or deployment row is written unless the requested module's exact current revision is validated and has a ready local image. Inspect the stable `code` and `build_status` fields: `endpoint_revision_not_ready`/`revision_not_ready` means validation is absent or belongs to another revision; `endpoint_image_not_ready` distinguishes `missing`, `queued`, `building`, `failed`, `pruned`, and `missing_local_image`.

```bash
# Inspect active, target, and previous build/revision; migration state; per-slot readiness/draining;
# rollback or rollout reason; and retained build-log links.
curl -fsS 'http://localhost:8000/bundle-endpoints/ENDPOINT_ID/deployment'
```

`legacy_fallback: true` with `migration_state: warming_managed` means the static pool intentionally remains live while target slots warm. `migration_failed` or a rollback reason means traffic remains on the legacy/active revision; inspect the target's `logs_url` and slot failure reason. `migration_state: managed`, `phase: ready`, and every desired slot reporting `ready: true` confirms atomic cutover. A target must never receive invocation traffic before that cutover.

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
