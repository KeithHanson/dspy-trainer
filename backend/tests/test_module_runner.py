import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.executor.module_runner import run_bundle_eval


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"


def test_run_bundle_eval_returns_items():
    result = run_bundle_eval(
        bundle_path=str(FIXTURES / "valid_bundle"),
        eval_inputs=[
            {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}},
        ],
        num_threads=1,
    )
    assert "score_pct" in result
    assert len(result["items"]) == 1
    assert result["items"][0]["input"]["question"] == "France capital?"
    assert result["items"][0]["rationale"] == "exact_match"
    assert result["items"][0]["flags"] == []
    assert result["items"][0]["raw_response"]["got"] == "Paris"


def test_run_bundle_eval_rejects_legacy_metric_return_type():
    with pytest.raises(Exception) as exc_info:
        run_bundle_eval(
            bundle_path=str(FIXTURES / "missing_eval_signature"),
            eval_inputs=[
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}},
            ],
            num_threads=1,
        )
    msg = str(exc_info.value)
    assert "judge_metric must return a dict" in msg or "unsupported operand type" in msg
