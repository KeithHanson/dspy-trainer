import dspy


class JudgeSignature(dspy.Signature):
    """Evaluate agent output against label expectations."""

    question = dspy.InputField()
    expected_answer = dspy.InputField()
    predicted_category = dspy.InputField()
    predicted_priority = dspy.InputField()
    predicted_reply = dspy.InputField()

    score = dspy.OutputField(desc="Float 0.0 to 1.0")
    rationale = dspy.OutputField(desc="One short sentence")
    flags_csv = dspy.OutputField(desc="Comma-separated failure flags, or none")


def judge_metric(example, prediction, trace=None):
    expected = getattr(example, "label", {}) if hasattr(example, "label") else {}
    if not isinstance(expected, dict):
        expected = {}

    expected_answer = str(expected.get("expected", "")).strip()

    category = str(getattr(prediction, "category", "")).strip().lower()
    priority = str(getattr(prediction, "priority", "")).strip().lower()
    reply = str(getattr(prediction, "reply", "")).strip()

    question = str(getattr(example, "question", ""))
    judge = dspy.Predict(JudgeSignature)
    verdict = judge(
        question=question,
        expected_answer=expected_answer,
        predicted_category=category,
        predicted_priority=priority,
        predicted_reply=reply,
    )

    try:
        score = float(str(getattr(verdict, "score", "0")).strip())
    except ValueError:
        score = 0.0
    score = min(1.0, max(0.0, score))

    flags_raw = str(getattr(verdict, "flags_csv", "")).strip()
    failed_flags = []
    if flags_raw and flags_raw.lower() not in {"none", "n/a", "na"}:
        failed_flags = [item.strip() for item in flags_raw.split(",") if item.strip()]

    rationale = str(getattr(verdict, "rationale", "")).strip() or "No rationale provided"

    return {
        "score": score,
        "rationale": rationale,
        "flags": failed_flags,
        "raw_response": {
            "expected_answer": expected_answer,
            "predicted_category": category,
            "predicted_priority": priority,
            "predicted_reply": reply,
        },
    }
