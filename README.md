# dspy-trainer

Developer bootstrap and compose operations are documented in `docs/COMPOSE_RUNBOOK.md`.
Bundle uploads used for module validation/runs are now stored on a shared Docker volume so both `backend` and `worker` resolve the same bundle path.
Optimization artifacts are also stored on a shared Docker volume so `backend` can materialize succeeded optimization results into new runnable bundles.
Optimizations can now materialize a new runnable bundle by copying the source bundle, adding the saved DSPy program state artifact, and updating `bundle.toml` so trainer runtime loads the optimized state automatically.
# opencode-sqlite-vector-search
