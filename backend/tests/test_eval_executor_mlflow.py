import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.executor.eval import run_eval_job


class FakeServices:
    def __init__(self, with_existing_mlflow=False, fail_trace=False):
        self.settings = type("S", (), {"mlflow_tracking_uri": "http://localhost:5001"})()
        self.job = {
            "id": "job-1",
            "status": "queued",
            "eval_name": "calm-amber-orbit",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "scenario_id": "scenario-1",
            "dataset_version": "v1",
            "bundle_path": "examples/module_bundles/simple_echo_agent",
            "repeat_count": 1,
            "num_threads": 1,
            "eval_inputs": [{"input": {"text": "a"}, "label": {"expected": "a"}}],
            "mlflow_experiment_id": "exp-existing" if with_existing_mlflow else None,
            "mlflow_parent_run_id": "run-parent-existing" if with_existing_mlflow else None,
            "failure_reason": None,
        }
        self.items = {}
        self.item_counter = 0
        self.mlflow_calls = []
        self.fail_trace = fail_trace

    async def get_eval_job(self, eval_job_id):
        if eval_job_id != self.job["id"]:
            return None
        return {**self.job, "eval_job_id": self.job["id"]}

    async def set_eval_job_status(self, eval_job_id, status, failure_reason=None):
        self.job["status"] = status
        self.job["failure_reason"] = failure_reason
        return await self.get_eval_job(eval_job_id)

    async def ensure_mlflow_experiment(self, project_id):
        self.mlflow_calls.append(("ensure_mlflow_experiment", project_id))
        return "exp-1"

    async def create_mlflow_run(self, experiment_id, run_name, tags):
        self.mlflow_calls.append(("create_mlflow_run", experiment_id, run_name, tags))
        if self.fail_trace and run_name.startswith("eval-item:"):
            raise RuntimeError("trace create failed")
        if not run_name.startswith("eval-item:"):
            return "run-parent-1"
        return "trace-1"

    async def set_mlflow_run_tag(self, run_id, key, value):
        self.mlflow_calls.append(("set_mlflow_run_tag", run_id, key, value))

    async def finalize_mlflow_run(self, run_id, status="FINISHED"):
        self.mlflow_calls.append(("finalize_mlflow_run", run_id, status))

    async def set_eval_job_mlflow(self, eval_job_id, mlflow_experiment_id, mlflow_parent_run_id):
        self.job["mlflow_experiment_id"] = mlflow_experiment_id
        self.job["mlflow_parent_run_id"] = mlflow_parent_run_id
        return await self.get_eval_job(eval_job_id)

    async def create_eval_run_item(self, **kwargs):
        self.item_counter += 1
        item_id = f"item-{self.item_counter}"
        self.items[item_id] = {"id": item_id, **kwargs}
        return {"id": item_id, "eval_run_item_id": item_id}

    async def set_eval_run_item_trace_id(self, eval_run_item_id, mlflow_trace_id):
        self.items[eval_run_item_id]["mlflow_trace_id"] = mlflow_trace_id
        return True

    async def set_eval_run_item_mlflow_run_id(self, eval_run_item_id, mlflow_item_run_id):
        return await self.set_eval_run_item_trace_id(eval_run_item_id, mlflow_item_run_id)


def test_run_eval_job_emits_parent_and_item_trace_with_correlation_tags(monkeypatch):
    monkeypatch.setattr(
        "app.executor.eval.run_bundle_eval",
        lambda bundle_path, eval_inputs, num_threads=1: {
            "score_pct": 100.0,
            "judge_instructions": "pass/fail",
            "items": [
                {
                    "item_index": 0,
                    "score": 1.0,
                    "input": {"question": "a"},
                    "label": {"expected": "a"},
                    "prediction": {"answer": "a"},
                    "rationale": "exact_match",
                }
            ],
        },
    )
    services = FakeServices()

    result = asyncio.run(run_eval_job(services, "job-1"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert services.job["mlflow_experiment_id"] == "exp-1"
    assert services.job["mlflow_parent_run_id"] == "run-parent-1"
    assert services.items["item-1"]["mlflow_trace_id"] is None

    item_run_calls = [call for call in services.mlflow_calls if call[0] == "create_mlflow_run" and call[2].startswith("eval-item:")]
    assert not item_run_calls


def test_run_eval_job_reuses_existing_mlflow_job_ids(monkeypatch):
    monkeypatch.setattr(
        "app.executor.eval.run_bundle_eval",
        lambda bundle_path, eval_inputs, num_threads=1: {
            "score_pct": 100.0,
            "judge_instructions": "pass/fail",
            "items": [
                {
                    "item_index": 0,
                    "score": 1.0,
                    "input": {"question": "a"},
                    "label": {"expected": "a"},
                    "prediction": {"answer": "a"},
                    "rationale": "exact_match",
                }
            ],
        },
    )
    services = FakeServices(with_existing_mlflow=True)

    result = asyncio.run(run_eval_job(services, "job-1"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert not any(call[0] == "ensure_mlflow_experiment" for call in services.mlflow_calls)
    parent_calls = [call for call in services.mlflow_calls if call[0] == "create_mlflow_run" and not call[2].startswith("eval-item:")]
    assert not parent_calls


def test_run_eval_job_fails_with_diagnostics_when_trace_emission_fails(monkeypatch):
    monkeypatch.setattr(
        "app.executor.eval.run_bundle_eval",
        lambda bundle_path, eval_inputs, num_threads=1: {
            "score_pct": 100.0,
            "judge_instructions": "pass/fail",
            "items": [
                {
                    "item_index": 0,
                    "score": 1.0,
                    "input": {"question": "a"},
                    "label": {"expected": "a"},
                    "prediction": {"answer": "a"},
                    "rationale": "exact_match",
                }
            ],
        },
    )
    services = FakeServices(fail_trace=True)

    result = asyncio.run(run_eval_job(services, "job-1"))

    assert result is not None
    assert result["status"] == "succeeded"
