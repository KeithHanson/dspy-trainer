import dspy


class TicketSignature(dspy.Signature):
    """Classify support requests and draft replies."""

    ticket = dspy.InputField(desc="New support ticket text")
    history = dspy.InputField(desc="Prior thread context")
    category = dspy.OutputField(desc="Issue category")
    priority = dspy.OutputField(desc="Priority from low/medium/high")
    reply = dspy.OutputField(desc="Suggested customer response")


class ParseIssueSignature(dspy.Signature):
    """Parse a user issue into triage fields."""

    question = dspy.InputField(desc="Raw user issue")
    ticket = dspy.OutputField(desc="Normalized ticket summary")
    history = dspy.OutputField(desc="Prior context if present, else empty")


class TriageAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.respond = dspy.ChainOfThought(TicketSignature)

    def forward(self, ticket: str, history: str):
        return self.respond(ticket=ticket, history=history)


class SingleInputTriageAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.parse_issue = dspy.ChainOfThought(ParseIssueSignature)
        self.agent = TriageAgent()

    def forward(self, question: str):
        parsed = self.parse_issue(question=question)
        ticket = str(getattr(parsed, "ticket", "")).strip()
        history = str(getattr(parsed, "history", "")).strip()
        prediction = self.agent(ticket=ticket, history=history)
        return dspy.Prediction(
            category=getattr(prediction, "category", ""),
            priority=getattr(prediction, "priority", ""),
            reply=getattr(prediction, "reply", ""),
        )


def build_program():
    return SingleInputTriageAgent()
