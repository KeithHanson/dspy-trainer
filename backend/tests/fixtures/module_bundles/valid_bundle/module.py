import dspy
from dspy.utils import DummyLM


class QA(dspy.Signature):
    question = dspy.InputField()
    answer = dspy.OutputField()


class Program(dspy.Module):
    def __init__(self):
        super().__init__()
        self.predict = dspy.Predict(QA)

    def forward(self, question: str):
        return self.predict(question=question)


def build_program() -> dspy.Module:
    return Program()


def build_lm():
    return DummyLM(
        [
            {"answer": "Paris"},
        ]
    )
