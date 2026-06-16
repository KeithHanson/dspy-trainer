# Example DSPy Bundle

This sample bundle includes the minimum required files:

- `module.py`
- `metric.py`
- `bundle.toml`
- `requirements.txt`
- `run_agent.py`

Use it as a baseline, update the signature and metric contract for your use case,
then push it to GitHub and import the repo in the web app for validation.

## Local feedback loop

Before you evaluate or optimize, you can run the sample agent locally from the command line:

```bash
python run_agent.py \
  --question "Customer says their refund never arrived and they already emailed twice." \
  --model openai/gpt-4o-mini
```

The script prints the raw prediction result returned by the program. For the sample bundle that will usually look like:

```json
{
  "category": "billing",
  "priority": "high",
  "reply": "..."
}
```

You can also set credentials through environment variables instead of flags:

- `DSPY_MODEL` or `OPENAI_MODEL`
- `DSPY_API_BASE` or `OPENAI_API_BASE`
- `DSPY_API_KEY` or `OPENAI_API_KEY`

## Optional system dependency commands

If your bundle needs OS libraries before `requirements.txt` can be installed, declare them in `bundle.toml`:

```toml
[runtime]
system_dependency_commands = [
  "apt-get update",
  "apt-get install -y --no-install-recommends unixodbc unixodbc-dev libodbc2",
]
```

These commands run before the bundle `requirements.txt` install step inside the backend/worker container.
