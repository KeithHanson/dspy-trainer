def judge_metric(example, prediction) -> dict[str, object]:
    expected = str(example.label.get("expected", ""))
    got = str(prediction.answer)
    matched = expected == got
    return {
        "score": 1.0 if matched else 0.0,
        "rationale": "exact_match" if matched else "mismatch",
        "flags": [] if matched else ["answer_mismatch"],
        "raw_response": {"expected": expected, "got": got},
    }
