JUDGE_INSTRUCTIONS = "Return pass when answer matches expected exactly."


def judge_metric(example, prediction) -> bool:
    expected = str(example.label.get("expected", ""))
    got = str(prediction.answer)
    return expected == got
