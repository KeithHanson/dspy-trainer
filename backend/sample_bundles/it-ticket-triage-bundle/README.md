# IT Ticket Triage Sample Bundle

This sample bundle demonstrates a simple helpdesk triage agent.

Inputs:

- `ticket_title`
- `ticket_body`

Outputs:

- `priority`
- `category`
- `reply`

The metric uses an LLM judge and scores three dimensions evenly:

- priority accuracy
- category accuracy
- whether the reply is on-topic and polite

Run it locally:

```bash
python run_agent.py \
  --ticket-title "VPN not connecting" \
  --ticket-body "I am traveling and cannot connect to the company VPN from my laptop." \
  --model openai/gpt-4o-mini
```

Example evaluation row:

```json
{
  "input": {
    "ticket_title": "Payroll portal locked",
    "ticket_body": "I entered my password too many times and now I cannot get into payroll."
  },
  "label": {
    "expected_priority": "medium",
    "expected_category": "access",
    "response_expectations": "Acknowledge the lockout, stay polite, and tell the user the next step for regaining access."
  }
}
```
