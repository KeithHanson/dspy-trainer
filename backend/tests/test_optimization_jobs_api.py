import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


MODULES = {"mod-1"}
JOBS: dict[str, dict] = {}
NEXT_JOB_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_optimization_job(
    self,
    project_id,
    module_import_id,
    bundle_path,
    train_inputs,
    val_inputs,
    num_threads,
    source_eval_job_id,
):
    global NEXT_JOB_ID
    if module_import_id not in MODULES:
        return None
    job_id = f"opt-{NEXT_JOB_ID}"
    NEXT_JOB_ID += 1
    job = {
        "id": job_id,
        "status": "queued",
        "project_id": project_id,
        "module_import_id": module_import_id,
        "bundle_path": bundle_path,
        "train_inputs": train_inputs,
        "val_inputs": val_inputs,
        "num_threads": num_threads,
        "source_eval_job_id": source_eval_job_id,
        "artifact_path": None,
        "failure_reason": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    JOBS[job_id] = job
    return job


async def fake_get_optimization_job(self, optimization_job_id):
    return JOBS.get(optimization_job_id)


async def fake_cancel_optimization_job(self, optimization_job_id):
    job = JOBS.get(optimization_job_id)
    if job is None:
        return None
    if job["status"] in {"queued", "running"}:
        job["status"] = "canceled"
    return job


async def fake_run_optimization_job(self, optimization_job_id):
    job = JOBS.get(optimization_job_id)
    if job is None:
        return None
    if job["status"] == "canceled":
        return job
    job["status"] = "succeeded"
    job["artifact_path"] = f"optimization://{optimization_job_id}/score-100.0"
    return job


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_optimization_job", fake_create_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "cancel_optimization_job", fake_cancel_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "run_optimization_job", fake_run_optimization_job)


def _reset_state():
    global NEXT_JOB_ID
    JOBS.clear()
    NEXT_JOB_ID = 1


def test_optimization_job_create_get_run_cancel(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/optimization/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "train_inputs": [{"input": {"question": "x"}, "label": {"expected": "y"}}],
                "val_inputs": [{"input": {"question": "x"}, "label": {"expected": "y"}}],
                "num_threads": 2,
                "source_eval_job_id": "job-1",
            },
        )
        assert created.status_code == 200
        job_id = created.json()["id"]

        fetched = client.get(f"/optimization/jobs/{job_id}")
        assert fetched.status_code == 200
        assert fetched.json()["bundle_path"] == "examples/module_bundles/simple_echo_agent"

        ran = client.post(f"/optimization/jobs/{job_id}/run")
        assert ran.status_code == 200
        assert ran.json()["status"] == "succeeded"
        assert ran.json()["artifact_path"]

        canceled = client.post(f"/optimization/jobs/{job_id}/cancel")
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "succeeded"


def test_optimization_job_not_found_paths(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        missing_create = client.post(
            "/optimization/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "missing",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "train_inputs": [],
                "val_inputs": [],
            },
        )
        assert missing_create.status_code == 404

        assert client.get("/optimization/jobs/missing").status_code == 404
        assert client.post("/optimization/jobs/missing/run").status_code == 404
        assert client.post("/optimization/jobs/missing/cancel").status_code == 404
