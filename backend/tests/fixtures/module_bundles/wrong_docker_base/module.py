import dspy


class QA(dspy.Signature):
    question = dspy.InputField()
    answer = dspy.OutputField()


class Program:
    pass
