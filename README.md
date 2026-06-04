# dspy-trainer

Developer bootstrap and compose operations are documented in `docs/COMPOSE_RUNBOOK.md`.
GitHub bundle access is configured server-side through `GITHUB_PAT` / `DSPY_TRAINER_GITHUB_PAT`; the web UI does not collect or store personal access tokens.
GitHub-backed bundle checkouts are stored on shared container-accessible paths so both `backend` and `worker` resolve the same validated checkout, and each tracked module can optionally point at a bundle subfolder inside that repo.
Optimization artifacts are also stored on a shared Docker volume so `backend` can validate succeeded optimization results, push them to an `optimization-<job-prefix>` branch, and preserve the tracked module on its configured base branch until the operator manually merges and resyncs.
