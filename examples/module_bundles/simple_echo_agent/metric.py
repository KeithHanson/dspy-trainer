JUDGE_INSTRUCTIONS = "Pass only when the final answer exactly matches the expected string."


def judge_metric(example, prediction) -> dict[str, object]:
    expected = str(example.label.get("expected", "")).strip()
    got = str(prediction.answer).strip()
    matched = got == expected
    return {
        "score": 1.0 if matched else 0.0,
        "rationale": "exact_match" if matched else "mismatch",
        "flags": [] if matched else ["answer_mismatch"],
        "raw_response": {"expected": expected, "got": got},
    }
