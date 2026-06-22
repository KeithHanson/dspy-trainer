import dspy


class TicketJudgeSignature(dspy.Signature):
    """Judge an IT ticket triage result against expected outcomes."""

    ticket_title = dspy.InputField()
    ticket_body = dspy.InputField()
    expected_priority = dspy.InputField()
    expected_category = dspy.InputField()
    response_expectations = dspy.InputField()
    predicted_priority = dspy.InputField()
    predicted_category = dspy.InputField()
    predicted_reply = dspy.InputField()

    priority_correct = dspy.OutputField(desc="Return 1 if the priority is accurate, else 0")
    category_correct = dspy.OutputField(desc="Return 1 if the category is accurate, else 0")
    response_quality = dspy.OutputField(desc="Return 1 if the reply is on-topic and polite, else 0")
    rationale = dspy.OutputField(desc="One short paragraph explaining the judgment")


def _to_binary_score(value) -> float:
    normalized = str(value or "").strip().lower()
    return 1.0 if normalized in {"1", "true", "yes", "pass"} else 0.0


def judge_metric(example, prediction, trace=None):
    judge = dspy.Predict(TicketJudgeSignature)
    verdict = judge(
        ticket_title=example.ticket_title,
        ticket_body=example.ticket_body,
        expected_priority=str(example.label.get("expected_priority", "")).strip(),
        expected_category=str(example.label.get("expected_category", "")).strip(),
        response_expectations=str(example.label.get("response_expectations", "")).strip(),
        predicted_priority=str(getattr(prediction, "priority", "")).strip(),
        predicted_category=str(getattr(prediction, "category", "")).strip(),
        predicted_reply=str(getattr(prediction, "reply", "")).strip(),
    )

    priority_score = _to_binary_score(getattr(verdict, "priority_correct", 0))
    category_score = _to_binary_score(getattr(verdict, "category_correct", 0))
    response_score = _to_binary_score(getattr(verdict, "response_quality", 0))
    total_score = (priority_score + category_score + response_score) / 3.0

    flags = []
    if priority_score < 1.0:
        flags.append("priority_incorrect")
    if category_score < 1.0:
        flags.append("category_incorrect")
    if response_score < 1.0:
        flags.append("reply_off_topic_or_impolite")

    return {
        "score": total_score,
        "rationale": str(getattr(verdict, "rationale", "")).strip() or "LLM judge completed triage review.",
        "flags": flags,
        "raw_response": {
            "expected_priority": example.label.get("expected_priority"),
            "expected_category": example.label.get("expected_category"),
            "response_expectations": example.label.get("response_expectations"),
            "predicted_priority": getattr(prediction, "priority", ""),
            "predicted_category": getattr(prediction, "category", ""),
            "predicted_reply": getattr(prediction, "reply", ""),
            "priority_score": priority_score,
            "category_score": category_score,
            "response_score": response_score,
        },
    }
