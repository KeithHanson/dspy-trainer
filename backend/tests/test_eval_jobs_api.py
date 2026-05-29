import sys
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


MODULES = {"mod-1"}
EVAL_JOBS: dict[str, dict] = {}
EVAL_ITEMS: dict[str, list[dict]] = {}
NEXT_JOB_ID = 1
NEXT_ITEM_ID = 1
NEXT_TRACE_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_eval_job(
    self,
    project_id,
    module_import_id,
    scenario_id,
    dataset_version,
    bundle_path,
    repeat_count,
    num_threads,
    eval_inputs,
    evaluation_plan_id,
    mlflow_experiment_id,
    mlflow_parent_run_id,
):
    global NEXT_JOB_ID
    if module_import_id not in MODULES:
        return None
    job_id = f"job-{NEXT_JOB_ID}"
    NEXT_JOB_ID += 1
    job = {
        "id": job_id,
        "status": "queued",
        "project_id": project_id,
        "module_import_id": module_import_id,
        "scenario_id": scenario_id,
        "dataset_version": dataset_version,
        "bundle_path": bundle_path,
        "repeat_count": repeat_count,
        "num_threads": num_threads,
        "eval_inputs": eval_inputs,
        "evaluation_plan_id": evaluation_plan_id,
        "mlflow_experiment_id": mlflow_experiment_id,
        "mlflow_parent_run_id": mlflow_parent_run_id,
        "failure_reason": None,
        "eval_job_id": job_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    EVAL_JOBS[job_id] = job
    EVAL_ITEMS[job_id] = []
    return job


async def fake_get_eval_job(self, eval_job_id):
    return EVAL_JOBS.get(eval_job_id)


async def fake_cancel_eval_job(self, eval_job_id):
    job = EVAL_JOBS.get(eval_job_id)
    if job is None:
        return None
    if job["status"] in {"queued", "running"}:
        job["status"] = "canceled"
        job["updated_at"] = "2026-01-01T00:01:00+00:00"
    return job


async def fake_seed_eval_run_items(self, eval_job_id, count, initial_status="queued"):
    global NEXT_ITEM_ID
    job = EVAL_JOBS.get(eval_job_id)
    if job is None:
        raise ValueError("eval job not found")
    created = []
    for _ in range(count):
        item_id = f"item-{NEXT_ITEM_ID}"
        NEXT_ITEM_ID += 1
        item = {
            "id": item_id,
            "eval_run_item_id": item_id,
            "eval_job_id": eval_job_id,
            "status": initial_status,
            "project_id": job["project_id"],
            "module_import_id": job["module_import_id"],
            "scenario_id": job["scenario_id"],
            "dataset_version": job["dataset_version"],
            "mlflow_experiment_id": job["mlflow_experiment_id"],
            "mlflow_parent_run_id": job["mlflow_parent_run_id"],
            "mlflow_trace_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        EVAL_ITEMS[eval_job_id].append(item)
        created.append(item)
    return created


async def fake_list_eval_run_items(self, eval_job_id, limit, offset):
    if eval_job_id not in EVAL_JOBS:
        return None
    items = EVAL_ITEMS.get(eval_job_id, [])
    page = items[offset : offset + limit]
    return {
        "items": page,
        "limit": limit,
        "offset": offset,
        "count": len(page),
        "total": len(items),
    }


async def fake_set_eval_job_status(self, eval_job_id, status, failure_reason=None):
    job = EVAL_JOBS.get(eval_job_id)
    if job is None:
        return None
    job["status"] = status
    job["failure_reason"] = failure_reason
    job["updated_at"] = "2026-01-01T00:02:00+00:00"
    return job


async def fake_create_eval_run_item(
    self,
    eval_job_id,
    status,
    repeat_index,
    item_index,
    score,
    input_payload,
    prediction_payload,
    label_payload,
    rationale,
    mlflow_item_run_id,
    mlflow_trace_id,
):
    global NEXT_ITEM_ID
    job = EVAL_JOBS.get(eval_job_id)
    if job is None:
        return None
    item_id = f"item-{NEXT_ITEM_ID}"
    NEXT_ITEM_ID += 1
    item = {
        "id": item_id,
        "eval_run_item_id": item_id,
        "eval_job_id": eval_job_id,
        "status": status,
        "project_id": job["project_id"],
        "module_import_id": job["module_import_id"],
        "scenario_id": job["scenario_id"],
        "dataset_version": job["dataset_version"],
        "mlflow_experiment_id": job["mlflow_experiment_id"],
        "mlflow_parent_run_id": job["mlflow_parent_run_id"],
        "mlflow_item_run_id": mlflow_item_run_id,
        "mlflow_trace_id": mlflow_trace_id,
        "repeat_index": repeat_index,
        "item_index": item_index,
        "score": score,
        "input_payload": input_payload,
        "prediction_payload": prediction_payload,
        "label_payload": label_payload,
        "rationale": rationale,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    EVAL_ITEMS[eval_job_id].append(item)
    return {"id": item_id, "eval_run_item_id": item_id, "eval_job_id": eval_job_id, "status": status}


async def fake_set_eval_job_mlflow(self, eval_job_id, mlflow_experiment_id, mlflow_parent_run_id):
    job = EVAL_JOBS.get(eval_job_id)
    if job is None:
        return None
    job["mlflow_experiment_id"] = mlflow_experiment_id
    job["mlflow_parent_run_id"] = mlflow_parent_run_id
    return job


async def fake_ensure_mlflow_experiment(self, project_id):
    return f"exp-{project_id}"


async def fake_create_mlflow_run(self, experiment_id, run_name, tags):
    global NEXT_TRACE_ID
    if not run_name.startswith("eval-item:"):
        return f"parent-{run_name}"
    trace_id = f"trace-{NEXT_TRACE_ID}"
    NEXT_TRACE_ID += 1
    return trace_id


async def fake_set_mlflow_run_tag(self, run_id, key, value):
    return None


async def fake_finalize_mlflow_run(self, run_id, status="FINISHED"):
    return None


async def fake_set_eval_run_item_trace_id(self, eval_run_item_id, mlflow_trace_id):
    for items in EVAL_ITEMS.values():
        for item in items:
            if item["id"] == eval_run_item_id:
                item["mlflow_trace_id"] = mlflow_trace_id
                return True
    return False


async def fake_set_eval_run_item_mlflow_run_id(self, eval_run_item_id, mlflow_item_run_id):
    return await fake_set_eval_run_item_trace_id(self, eval_run_item_id, mlflow_item_run_id)


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_eval_job", fake_create_eval_job)
    monkeypatch.setattr(main_mod.AppServices, "get_eval_job", fake_get_eval_job)
    monkeypatch.setattr(main_mod.AppServices, "cancel_eval_job", fake_cancel_eval_job)
    monkeypatch.setattr(main_mod.AppServices, "seed_eval_run_items", fake_seed_eval_run_items)
    monkeypatch.setattr(main_mod.AppServices, "list_eval_run_items", fake_list_eval_run_items)
    monkeypatch.setattr(main_mod.AppServices, "set_eval_job_status", fake_set_eval_job_status)
    monkeypatch.setattr(main_mod.AppServices, "create_eval_run_item", fake_create_eval_run_item)
    monkeypatch.setattr(main_mod.AppServices, "set_eval_job_mlflow", fake_set_eval_job_mlflow)
    monkeypatch.setattr(main_mod.AppServices, "ensure_mlflow_experiment", fake_ensure_mlflow_experiment)
    monkeypatch.setattr(main_mod.AppServices, "create_mlflow_run", fake_create_mlflow_run)
    monkeypatch.setattr(main_mod.AppServices, "set_mlflow_run_tag", fake_set_mlflow_run_tag)
    monkeypatch.setattr(main_mod.AppServices, "finalize_mlflow_run", fake_finalize_mlflow_run)
    monkeypatch.setattr(main_mod.AppServices, "set_eval_run_item_trace_id", fake_set_eval_run_item_trace_id)
    monkeypatch.setattr(main_mod.AppServices, "set_eval_run_item_mlflow_run_id", fake_set_eval_run_item_mlflow_run_id)


def _reset_state():
    global NEXT_JOB_ID
    global NEXT_ITEM_ID
    global NEXT_TRACE_ID
    EVAL_JOBS.clear()
    EVAL_ITEMS.clear()
    NEXT_JOB_ID = 1
    NEXT_ITEM_ID = 1
    NEXT_TRACE_ID = 1


def test_eval_job_create_get_cancel_and_terminal_cancel_noop(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        created = client.post(
            "/eval/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "scenario_id": "scenario-a",
                "dataset_version": "v1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "mlflow_experiment_id": "exp-1",
                "mlflow_parent_run_id": "run-parent-1",
            },
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["status"] == "queued"
        assert payload["eval_job_id"] == payload["id"]

        fetched = client.get(f"/eval/jobs/{payload['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == payload["id"]

        canceled = client.post(f"/eval/jobs/{payload['id']}/cancel")
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "canceled"

        EVAL_JOBS[payload["id"]]["status"] = "succeeded"
        terminal_cancel = client.post(f"/eval/jobs/{payload['id']}/cancel")
        assert terminal_cancel.status_code == 200
        assert terminal_cancel.json()["status"] == "succeeded"


def test_eval_job_not_found_paths(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        create_missing_module = client.post(
            "/eval/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "missing-module",
                "scenario_id": "scenario-a",
                "dataset_version": "v1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
            },
        )
        assert create_missing_module.status_code == 404

        get_missing = client.get("/eval/jobs/missing")
        assert get_missing.status_code == 404

        cancel_missing = client.post("/eval/jobs/missing/cancel")
        assert cancel_missing.status_code == 404

        list_missing = client.get("/eval/jobs/missing/items")
        assert list_missing.status_code == 404


def test_eval_job_list_items_with_pagination(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        created = client.post(
            "/eval/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "scenario_id": "scenario-a",
                "dataset_version": "v1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
            },
        )
        job_id = created.json()["id"]

        services = main_mod.app.state.services
        asyncio.run(services.seed_eval_run_items(job_id, count=5, initial_status="queued"))

        first = client.get(f"/eval/jobs/{job_id}/items", params={"limit": 2, "offset": 0})
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["count"] == 2
        assert first_payload["total"] == 5
        assert first_payload["limit"] == 2
        assert first_payload["offset"] == 0
        assert first_payload["items"][0]["eval_job_id"] == job_id
        assert first_payload["items"][0]["eval_run_item_id"] == "item-1"

        second = client.get(f"/eval/jobs/{job_id}/items", params={"limit": 2, "offset": 2})
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["count"] == 2
        assert second_payload["total"] == 5
        assert second_payload["items"][0]["eval_run_item_id"] == "item-3"

        third = client.get(f"/eval/jobs/{job_id}/items", params={"limit": 2, "offset": 4})
        assert third.status_code == 200
        third_payload = third.json()
        assert third_payload["count"] == 1
        assert third_payload["total"] == 5
        assert third_payload["items"][0]["eval_run_item_id"] == "item-5"


def test_eval_job_run_repeats_and_persists_items(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)

    async def fake_run_eval_job(services, eval_job_id):
        from app.executor.eval import run_eval_job

        return await run_eval_job(services, eval_job_id)

    monkeypatch.setattr(main_mod, "run_eval_job", fake_run_eval_job)
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
                    "label": {"expected": "Paris"},
                    "prediction": {"answer": "Paris"},
                    "rationale": "exact_match",
                },
                {
                    "item_index": 1,
                    "score": 1.0,
                    "input": {"question": "b"},
                    "label": {"expected": "Paris"},
                    "prediction": {"answer": "Paris"},
                    "rationale": "exact_match",
                },
            ],
        },
    )

    with TestClient(main_mod.app) as client:
        created = client.post(
            "/eval/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "scenario_id": "scenario-a",
                "dataset_version": "v1",
                "repeat_count": 2,
                "num_threads": 3,
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "eval_inputs": [
                    {"input": {"text": "a"}, "label": {"expected": "a"}},
                    {"input": {"text": "b"}, "label": {"expected": "b"}},
                ],
            },
        )
        job_id = created.json()["id"]

        ran = client.post(f"/eval/jobs/{job_id}/run")
        assert ran.status_code == 200
        assert ran.json()["status"] == "succeeded"

        items = client.get(f"/eval/jobs/{job_id}/items").json()
        assert items["total"] == 4
        assert items["items"][0]["repeat_index"] == 0
        assert items["items"][0]["item_index"] == 0
        assert items["items"][0]["score"] == 1.0
