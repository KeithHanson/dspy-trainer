# dspy-trainer

Developer bootstrap and compose operations are documented in `docs/COMPOSE_RUNBOOK.md`.
GitHub bundle access is configured server-side through `GITHUB_PAT` / `DSPY_TRAINER_GITHUB_PAT`; the web UI does not collect or store personal access tokens.
GitHub-backed bundle checkouts are stored on shared container-accessible paths so both `backend` and `worker` resolve the same validated checkout.
Optimization artifacts are also stored on a shared Docker volume so `backend` can materialize succeeded optimization results into new runnable bundles.
# opencode-sqlite-vector-search
