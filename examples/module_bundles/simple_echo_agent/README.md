# Simple Echo Agent Bundle

This example shows the minimum authoring surface:

- `module.py`: DSPy signature/module and `build_program()`
- `metric.py`: `JUDGE_INSTRUCTIONS` and `judge_metric(example, prediction)` returning pass/fail
- `eval_inputs.json`: simple inputs and expected labels

## Run It Through dspy-trainer

From repository root:

```bash
docker compose up -d --build backend postgres redis mlflow litellm-proxy
```

The example uses a real LM through LiteLLM. Ensure these are set (for compose defaults, `DSPY_TRAINER_LITELLM_API_KEY` is usually your `LITELLM_MASTER_KEY`):

```bash
export DSPY_TRAINER_LITELLM_BASE_URL=http://localhost:4000
export DSPY_TRAINER_LITELLM_API_KEY=sk-local-dev-master-key
export DSPY_TRAINER_LITELLM_MODEL=openai/codex-5.3
```

Create a module record:

```bash
MODULE_ID=$(curl -sS -X POST "http://localhost:8000/modules/import" \
  -H "Content-Type: application/json" \
  -d '{"source":"local","source_ref":"examples/module_bundles/simple_echo_agent"}' | python -c "import sys, json; print(json.load(sys.stdin)['id'])")
```

Validate contract:

```bash
curl -sS -X POST "http://localhost:8000/modules/${MODULE_ID}/validate" \
  -H "Content-Type: application/json" \
  -d '{"bundle_path":"examples/module_bundles/simple_echo_agent"}'
```

Run bundle eval (uses `module.py` + `metric.py` + `eval_inputs.json`):

```bash
curl -sS -X POST "http://localhost:8000/modules/${MODULE_ID}/smoke-test" \
  -H "Content-Type: application/json" \
  -d "{\"bundle_path\":\"examples/module_bundles/simple_echo_agent\",\"num_threads\":1,\"eval_inputs\":$(cat examples/module_bundles/simple_echo_agent/eval_inputs.json)}"
```

Get stored diagnostics/results:

```bash
curl -sS "http://localhost:8000/modules/${MODULE_ID}/diagnostics"
```
