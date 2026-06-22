def judge_metric(example, prediction, trace=None):
    expected = int(example.label["expected_r_count"])
    actual = int(getattr(prediction, "r_count", 0))
    passed = actual == expected

    return {
        "score": 1.0 if passed else 0.0,
        "rationale": (
            f"Count matched expected value {expected}."
            if passed
            else f"Expected {expected} r/R characters but got {actual}."
        ),
        "flags": [] if passed else ["wrong_r_count"],
        "raw_response": {
            "message": example.message,
            "expected_r_count": expected,
            "actual_r_count": actual,
        },
    }
