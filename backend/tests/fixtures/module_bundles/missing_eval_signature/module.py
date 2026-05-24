import dspy


class Program(dspy.Module):
    def forward(self, question: str):
        return dspy.Prediction(answer="x")


def build_program() -> dspy.Module:
    return Program()
