import dspy
import os


class RewriteQuestion(dspy.Signature):
    question = dspy.InputField()
    rewritten_question = dspy.OutputField()


class AnswerQuestion(dspy.Signature):
    rewritten_question = dspy.InputField()
    answer = dspy.OutputField()


class SimpleAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.rewrite = dspy.ChainOfThought(RewriteQuestion)
        self.answer = dspy.ChainOfThought(AnswerQuestion)

    def forward(self, question: str):
        step1 = self.rewrite(question=question)
        step2 = self.answer(rewritten_question=step1.rewritten_question)
        return dspy.Prediction(answer=step2.answer, rewritten_question=step1.rewritten_question)


def build_lm() -> dspy.LM:
    litellm_base_url = os.getenv("DSPY_TRAINER_LITELLM_BASE_URL", "http://litellm-proxy:4000")
    litellm_api_key = os.getenv("DSPY_TRAINER_LITELLM_API_KEY", "")
    model_name = os.getenv("DSPY_TRAINER_LITELLM_MODEL", "openai/codex-5.3")

    if not litellm_api_key:
        raise RuntimeError("DSPY_TRAINER_LITELLM_API_KEY is required for example bundle execution")

    return dspy.LM(
        model=model_name,
        api_base=litellm_base_url,
        api_key=litellm_api_key,
        model_type="responses",
    )


def build_program() -> dspy.Module:
    return SimpleAgent()
