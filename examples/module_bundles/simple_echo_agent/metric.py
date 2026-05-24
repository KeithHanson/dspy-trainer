JUDGE_INSTRUCTIONS = "Pass only when the final answer exactly matches the expected string."


def judge_metric(example, prediction) -> float:
    expected = str(example.label.get("expected", "")).strip()
    got = str(prediction.answer).strip()
    return 1.0 if got == expected else 0.0
