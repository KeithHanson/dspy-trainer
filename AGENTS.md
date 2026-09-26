# Agent Instructions

## Critical Folders and Files

This section MUST remain up to date after you complete a task.

- `AGENTS.md`: Operator and agent workflow rules for this repository.
- `README.md`: Project overview and high-level goals.
- `.env.sample`: Source of truth for required environment variables.
- `docs/COMPOSE_RUNBOOK.md`: Current compose bootstrap, health checks, and day-2 operations.
- `backend/app/config.py`: Validated runtime settings, including the deployment-wide bundle-install concurrency limit.
- `backend/app/services.py`: Central runtime services and PostgreSQL advisory-lock admission for bundle dependency installs.
- `backend/endpoint_worker.py`: Managed endpoint warmup, status transitions, and heartbeat lifecycle.
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
