import dspy


class ExtractEvent(dspy.Signature):
    """Extract event details from an email."""

    email = dspy.InputField()
    event_name = dspy.OutputField()
    date = dspy.OutputField()


class EventExtractionAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.extract = dspy.Predict(ExtractEvent)

    def forward(self, email: str):
        return self.extract(email=email)


def build_program():
    return EventExtractionAgent()
