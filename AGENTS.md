# Agent Instructions

## Critical Folders and Files

This section MUST remain up to date after you complete a task.

- `AGENTS.md`: Operator and agent workflow rules for this repository.
- `README.md`: Project overview and high-level goals.
- `.env.sample`: Source of truth for required environment variables.
- `docs/COMPOSE_RUNBOOK.md`: Current compose bootstrap, health checks, and day-2 operations.
- `backend/app/config.py`: Validated runtime settings, including the deployment-wide bundle-install concurrency limit.
- `backend/app/main.py`: FastAPI lifecycle and HTTP contracts, including revision-image status, bounded logs, retry, and rebuild-all operator APIs.
- `backend/app/services.py`: Central runtime services, PostgreSQL schema/bootstrap, revision-image coordination/persistence, build-and-revision-pinned endpoint routing, and legacy bundle-install admission.
- `backend/app/revision_images.py`: Reusable revision-image build, endpoint-deployment, and managed-container state transitions and payload builders.
- `backend/app/revision_image_builder.py`: Deterministic revision snapshot context generation, Docker SDK adapter, provenance labels, and inspected local-image build results.
- `backend/app/revision_image_coordinator.py`: PostgreSQL advisory leadership, durable claim/recovery/fencing, priority ordering, supersession, retry/rebuild, and coordinator shutdown logic.
- `backend/app/endpoint_container_reconciler.py`: Advisory-led exact-image endpoint container rollout, rollback, drain, ownership, network discovery, and image-retention reconciliation.
- `backend/deployer.py`: Dedicated long-running deployer process entrypoint.
- `backend/Deployer.Dockerfile` and `backend/deployer-requirements.txt`: Deployer-only Docker SDK image boundary based on an immutable backend image ID.
- `backend/endpoint_worker.py`: Explicit managed image identity, baked-source endpoint execution, assignment validation, and build/revision heartbeat lifecycle.
- `docker-compose.yml`: Shared runtime configuration wiring for backend, worker, and endpoint-worker services.
- `backend/sample_bundles/`: Downloadable reference bundles exposed in the UI.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var
