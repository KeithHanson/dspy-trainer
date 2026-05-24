import sys
from pathlib import Path


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
