import dspy


class TicketTriageSignature(dspy.Signature):
    """Triage an inbound IT ticket and draft a polite response."""

    ticket_title = dspy.InputField(desc="Short summary of the issue")
    ticket_body = dspy.InputField(desc="Full ticket body with user context")
    priority = dspy.OutputField(desc="One of: low, medium, high, urgent")
    category = dspy.OutputField(desc="One of: access, hardware, software, network, security, billing, other")
    reply = dspy.OutputField(desc="Polite customer-facing response with the next step")


class TicketTriageAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.triage = dspy.ChainOfThought(TicketTriageSignature)

    def forward(self, ticket_title: str, ticket_body: str):
        return self.triage(ticket_title=ticket_title, ticket_body=ticket_body)


def build_program():
    return TicketTriageAgent()
