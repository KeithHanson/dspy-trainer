import dspy


class JudgeSignature(dspy.Signature):
    """Evaluate agent output against label-provided judge instructions."""

    question = dspy.InputField()
    judge_instructions = dspy.InputField()
    predicted_category = dspy.InputField()
    predicted_priority = dspy.InputField()
    predicted_reply = dspy.InputField()

    score = dspy.OutputField(desc="Float 0.0 to 1.0")
    rationale = dspy.OutputField(desc="One short sentence")
    flags_csv = dspy.OutputField(desc="Comma-separated failure flags, or none")


def judge_metric(example, prediction, trace=None):
    judge_instructions = example.label["judge_instructions"].strip()
    question = example.question
    category = prediction.category.strip().lower()
    priority = prediction.priority.strip().lower()
    reply = prediction.reply.strip()

    judge = dspy.Predict(JudgeSignature)
    verdict = judge(
        question=question,
        judge_instructions=judge_instructions,
        predicted_category=category,
        predicted_priority=priority,
        predicted_reply=reply,
    )

    score = max(0.0, min(1.0, float(verdict.score)))
    flags_raw = verdict.flags_csv.strip()
    failed_flags = [] if flags_raw.lower() in {"", "none", "n/a", "na"} else [item.strip() for item in flags_raw.split(",") if item.strip()]

    return {
        "score": score,
        "rationale": verdict.rationale.strip(),
        "flags": failed_flags,
        "raw_response": {
            "judge_instructions": judge_instructions,
            "predicted_category": category,
            "predicted_priority": priority,
            "predicted_reply": reply,
        },
    }
