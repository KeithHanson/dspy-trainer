import dspy


class CountRSignature(dspy.Signature):
    """Count how many r/R letters appear in a message."""

    message = dspy.InputField(desc="Any message to inspect")
    r_count = dspy.OutputField(desc="Number of r or R characters in the message")


class CountRAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.count = dspy.ChainOfThought(CountRSignature)

    def forward(self, message: str):
        return self.count(message=message)


def build_program():
    return CountRAgent()
