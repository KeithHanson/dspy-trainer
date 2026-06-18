import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.executor import eval as eval_mod


def test_recent_trace_ids_uses_metadata_only_search(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, tracking_uri):
            assert tracking_uri == "http://mlflow:5000"

        def search_traces(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(info=SimpleNamespace(trace_id="tr-1"))]

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("mlflow.tracking.MlflowClient", FakeClient)

    trace_ids = eval_mod._recent_trace_ids("http://mlflow:5000", "1", max_results=25)

    assert trace_ids == {"tr-1"}
    assert calls == [{"experiment_ids": ["1"], "max_results": 25, "include_spans": False}]


def test_list_parent_run_traces_uses_metadata_only_search(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, tracking_uri):
            assert tracking_uri == "http://mlflow:5000"

        def search_traces(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(info=SimpleNamespace(trace_id="tr-1", request_preview="q"))]

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("mlflow.tracking.MlflowClient", FakeClient)

    traces = eval_mod._list_parent_run_traces("http://mlflow:5000", "1", "run-1")

    assert len(traces) == 1
    assert calls == [{
        "experiment_ids": ["1"],
        "filter_string": "run_id = 'run-1'",
        "max_results": 500,
        "include_spans": False,
    }]
