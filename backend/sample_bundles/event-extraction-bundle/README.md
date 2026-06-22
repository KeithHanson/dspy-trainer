# Event Extraction Sample Bundle

This sample bundle mirrors the simple DSPy event-extraction pattern shown in the DSPy documentation.

Input:

- `email`

Outputs:

- `event_name`
- `date`

Run it locally:

```bash
python run_agent.py \
  --email "Hi team, the Winter Demo Day will be on February 14, 2027 at headquarters."
```

Example evaluation row:

```json
{
  "input": {
    "email": "Please join us for the Winter Demo Day on February 14, 2027 at headquarters."
  },
  "label": {
    "expected_event_name": "Winter Demo Day",
    "expected_date": "February 14, 2027"
  }
}
```
