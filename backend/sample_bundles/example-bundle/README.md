# Example DSPy Bundle

This downloadable sample bundle demonstrates a tiny DSPy agent that counts how
many `r` or `R` characters appear in any message.

It includes the minimum required files:

- `module.py`
- `metric.py`
- `bundle.toml`
- `requirements.txt`
- `run_agent.py`

Use it as a baseline, then replace the signature, module logic, and metric with
your own task before pushing the bundle to GitHub and importing it in the app.

## Local feedback loop

Run the sample agent locally:

```bash
python run_agent.py --message "RIVER ROAD RR"
```

Expected output:

```json
{
  "r_count": 5
}
```

The included metric expects evaluation rows shaped like this:

```json
{
  "input": {"message": "strawberry"},
  "label": {"expected_r_count": 3}
}
```

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
