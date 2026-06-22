def _normalize_text(value):
    return " ".join(str(value or "").strip().lower().split())


def judge_metric(example, prediction, trace=None):
    expected_event_name = str(example.label.get("expected_event_name", "")).strip()
    expected_date = str(example.label.get("expected_date", "")).strip()
    predicted_event_name = str(getattr(prediction, "event_name", "")).strip()
    predicted_date = str(getattr(prediction, "date", "")).strip()

    event_name_match = _normalize_text(predicted_event_name) == _normalize_text(expected_event_name)
    date_match = _normalize_text(predicted_date) == _normalize_text(expected_date)
    score = ((1.0 if event_name_match else 0.0) + (1.0 if date_match else 0.0)) / 2.0

    flags = []
    if not event_name_match:
        flags.append("event_name_mismatch")
    if not date_match:
        flags.append("date_mismatch")

    if event_name_match and date_match:
        rationale = "Event name and date matched the expected values."
    elif event_name_match:
        rationale = "Event name matched, but the date did not."
    elif date_match:
        rationale = "Date matched, but the event name did not."
    else:
        rationale = "Neither the event name nor the date matched the expected values."

    return {
        "score": score,
        "rationale": rationale,
        "flags": flags,
        "raw_response": {
            "expected_event_name": expected_event_name,
            "expected_date": expected_date,
            "predicted_event_name": predicted_event_name,
            "predicted_date": predicted_date,
        },
    }
