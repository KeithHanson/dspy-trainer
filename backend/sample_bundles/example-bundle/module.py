import dspy


class CountRSignature(dspy.Signature):
    """Count how many r/R letters appear in a message."""

    message = dspy.InputField(desc="Any message to inspect")
    r_count = dspy.OutputField(desc="Number of r or R characters in the message")


class CountRAgent(dspy.Module):
    def forward(self, message: str):
        total = sum(1 for char in str(message) if char.lower() == "r")
        return dspy.Prediction(r_count=total)


def build_program():
    return CountRAgent()
