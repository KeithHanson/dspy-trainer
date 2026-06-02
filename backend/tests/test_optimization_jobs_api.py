import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


MODULES = {"mod-1"}
JOBS: dict[str, dict] = {}
DATASETS: dict[str, dict] = {}
NEXT_JOB_ID = 1
NEXT_DATASET_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_optimization_job(
    self,
    project_id,
    module_import_id,
    bundle_path,
    strategy,
    objective,
    dataset_id,
    validation_dataset_id,
    execution_lm_profile_id,
    helper_lm_profile_id,
    request_config,
    normalized_config,
    train_inputs,
    val_inputs,
    num_threads,
    source_eval_job_id,
):
    global NEXT_JOB_ID
    if module_import_id not in MODULES:
        return None
    if dataset_id is not None and dataset_id not in DATASETS:
        return None
    if validation_dataset_id is not None and validation_dataset_id not in DATASETS:
        return None
    job_id = f"opt-{NEXT_JOB_ID}"
    NEXT_JOB_ID += 1
    job = {
        "id": job_id,
        "status": "queued",
        "project_id": project_id,
        "module_import_id": module_import_id,
        "bundle_path": bundle_path,
        "strategy": strategy,
        "objective": objective,
        "dataset_id": dataset_id,
        "validation_dataset_id": validation_dataset_id,
        "execution_lm_profile_id": execution_lm_profile_id,
        "helper_lm_profile_id": helper_lm_profile_id,
        "request_config": request_config,
        "normalized_config": normalized_config,
        "train_inputs": train_inputs,
        "val_inputs": val_inputs,
        "num_threads": num_threads,
        "source_eval_job_id": source_eval_job_id,
        "artifact_path": None,
        "artifact_metadata": {},
        "telemetry_summary": {},
        "comparison_summary": {},
        "failure_reason": None,
        "run_started_at": None,
        "finished_at": None,
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


async def fake_list_optimization_jobs(self, limit=50, offset=0):
    jobs = list(JOBS.values())
    return jobs[offset: offset + limit]


async def fake_create_optimization_dataset(
    self,
    project_id,
    module_import_id,
    name,
    dataset_kind,
    source_type,
    source_eval_job_ids,
    source_filters,
    records,
    input_keys,
    label_keys,
    optimizer_contract,
    provenance_summary,
    notes,
):
    global NEXT_DATASET_ID
    if module_import_id not in MODULES:
        return None
    dataset_id = f"ods-{NEXT_DATASET_ID}"
    NEXT_DATASET_ID += 1
    dataset = {
        "id": dataset_id,
        "project_id": project_id,
        "module_import_id": module_import_id,
        "name": name,
        "dataset_kind": dataset_kind,
        "source_type": source_type,
        "source_eval_job_ids": source_eval_job_ids,
        "source_filters": source_filters,
        "records": records,
        "record_count": len(records),
        "input_keys": input_keys,
        "label_keys": label_keys,
        "optimizer_contract": optimizer_contract,
        "provenance_summary": provenance_summary,
        "notes": notes,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    DATASETS[dataset_id] = dataset
    return dataset


async def fake_get_optimization_dataset(self, dataset_id):
    return DATASETS.get(dataset_id)


async def fake_list_optimization_datasets(self, limit=50, offset=0):
    datasets = list(DATASETS.values())
    return datasets[offset: offset + limit]


async def fake_derive_optimization_dataset(
    self,
    project_id,
    module_import_id,
    name,
    dataset_kind,
    source_type,
    source_eval_job_ids,
    source_filters,
    notes,
    persist,
):
    if module_import_id not in MODULES or source_eval_job_ids == ["missing"]:
        return None
    payload = {
        "project_id": project_id,
        "module_import_id": module_import_id,
        "name": name,
        "dataset_kind": dataset_kind,
        "source_type": source_type,
        "source_eval_job_ids": source_eval_job_ids,
        "source_filters": source_filters,
        "records": [{"input": {"question": "x"}, "label": {"expected": "y"}}],
        "record_count": 1,
        "input_keys": ["question"],
        "label_keys": ["expected"],
        "optimizer_contract": "dspy_example_v1",
        "provenance_summary": {"included_records": 1},
        "notes": notes,
        "preview": not persist,
    }
    if persist:
        payload["id"] = "ods-derived-1"
    return payload


async def fake_run_optimization_job(self, optimization_job_id):
    job = JOBS.get(optimization_job_id)
    if job is None:
        return None
    if job["status"] == "canceled":
        return job
    job["status"] = "succeeded"
    job["artifact_path"] = f"optimization://{optimization_job_id}/score-100.0"
    job["artifact_metadata"] = {
        "artifact_type": "dspy_program_state",
        "artifact_dir": f"/tmp/dspy-trainer/optimization_artifacts/{optimization_job_id}",
        "program_state_path": f"/tmp/dspy-trainer/optimization_artifacts/{optimization_job_id}/program.json",
        "predictor_count": 1,
        "selected_demo_count": 1,
    }
    job["telemetry_summary"] = {
        "strategy": "gepa",
        "strategy_details": {"optimizer_class": "GEPA", "candidate_count": 3},
        "selected_demos": [{"predictor": "predict", "demo_count": 1, "demos": []}],
        "dataset_summary": {"requested_record_count": 3, "usable_record_count": 1},
    }
    job["comparison_summary"] = {
        "baseline_score_pct": 50.0,
        "optimized_score_pct": 100.0,
        "score_delta_pct": 50.0,
        "baseline_item_count": 1,
        "optimized_item_count": 1,
    }
    job["run_started_at"] = "2026-01-01T00:01:00+00:00"
    job["finished_at"] = "2026-01-01T00:02:00+00:00"
    return job


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_optimization_job", fake_create_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "list_optimization_jobs", fake_list_optimization_jobs)
    monkeypatch.setattr(main_mod.AppServices, "cancel_optimization_job", fake_cancel_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "run_optimization_job", fake_run_optimization_job)
    monkeypatch.setattr(main_mod.AppServices, "create_optimization_dataset", fake_create_optimization_dataset)
    monkeypatch.setattr(main_mod.AppServices, "get_optimization_dataset", fake_get_optimization_dataset)
    monkeypatch.setattr(main_mod.AppServices, "list_optimization_datasets", fake_list_optimization_datasets)
    monkeypatch.setattr(main_mod.AppServices, "derive_optimization_dataset", fake_derive_optimization_dataset)


def _reset_state():
    global NEXT_JOB_ID, NEXT_DATASET_ID
    JOBS.clear()
    DATASETS.clear()
    NEXT_JOB_ID = 1
    NEXT_DATASET_ID = 1


def test_optimization_job_create_get_run_cancel(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        dataset_created = client.post(
            "/optimization/datasets",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "name": "Eval Passes",
                "dataset_kind": "demo",
                "source_type": "eval_passes",
                "source_eval_job_ids": ["job-1"],
                "source_filters": {"score_threshold": 0.8},
                "records": [{"input": {"question": "x"}, "accepted_output": {"answer": "y"}}],
                "input_keys": ["question"],
                "label_keys": ["answer"],
                "optimizer_contract": "dspy_example_v1",
                "provenance_summary": {"accepted_run_outputs": 1},
            },
        )
        assert dataset_created.status_code == 200
        dataset_id = dataset_created.json()["id"]

        dataset_list = client.get("/optimization/datasets?limit=50&offset=0")
        assert dataset_list.status_code == 200
        assert len(dataset_list.json()) == 1

        derived_preview = client.post(
            "/optimization/datasets/derive",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "name": "Derived preview",
                "dataset_kind": "feedback",
                "source_type": "eval_feedback",
                "source_eval_job_ids": ["job-1"],
                "persist": False,
            },
        )
        assert derived_preview.status_code == 200
        assert derived_preview.json()["preview"] is True

        created = client.post(
            "/optimization/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "strategy": "gepa",
                "objective": "optimize_judge_feedback",
                "dataset_id": dataset_id,
                "validation_dataset_id": dataset_id,
                "execution_lm_profile_id": "lm-exec-1",
                "helper_lm_profile_id": "lm-help-1",
                "request_config": {"budget": "light"},
                "normalized_config": {"optimizer_family": "client_override"},
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
        assert fetched.json()["strategy"] == "gepa"
        assert fetched.json()["dataset_id"] == dataset_id
        assert fetched.json()["request_config"]["budget"] == "light"
        assert fetched.json()["request_config"]["_audit"]["execution_lm_profile_id"] == "lm-exec-1"
        assert fetched.json()["request_config"]["_audit"]["client_normalized_config"] == {
            "optimizer_family": "client_override"
        }
        assert fetched.json()["normalized_config"]["optimizer_family"] == "gepa"
        assert fetched.json()["normalized_config"]["optimizer_class"] == "GEPA"
        assert fetched.json()["normalized_config"]["compile_mode"] == "offline"
        assert fetched.json()["normalized_config"]["dspy_config"] == {
            "reflection_lm_profile_id": "lm-help-1",
            "auto": "light",
            "track_stats": True,
        }

        ran = client.post(f"/optimization/jobs/{job_id}/run")
        assert ran.status_code == 200
        ran_result = ran.json()
        assert ran_result["status"] == "succeeded"
        assert ran_result["artifact_path"]
        assert ran_result["artifact_metadata"]["artifact_type"] == "dspy_program_state"
        assert ran_result["telemetry_summary"]["strategy"] == "gepa"
        assert ran_result["comparison_summary"]["score_delta_pct"] == 50.0
        assert ran_result["run_started_at"] == "2026-01-01T00:01:00+00:00"
        assert ran_result["finished_at"] == "2026-01-01T00:02:00+00:00"

        listed = client.get("/optimization/jobs?limit=50&offset=0")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        persisted = listed.json()[0]
        assert persisted["artifact_path"] == ran_result["artifact_path"]
        assert persisted["artifact_metadata"]["artifact_type"] == "dspy_program_state"
        assert persisted["telemetry_summary"]["strategy"] == "gepa"
        assert persisted["telemetry_summary"]["strategy_details"]["optimizer_class"] == "GEPA"
        assert persisted["telemetry_summary"]["strategy_details"]["candidate_count"] == 3
        assert persisted["comparison_summary"]["baseline_score_pct"] == 50.0
        assert persisted["comparison_summary"]["optimized_score_pct"] == 100.0
        assert persisted["comparison_summary"]["score_delta_pct"] == 50.0
        assert persisted["run_started_at"] == "2026-01-01T00:01:00+00:00"
        assert persisted["finished_at"] == "2026-01-01T00:02:00+00:00"

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
                "strategy": "bootstrap_fewshot",
                "objective": "optimize_demo_quality",
                "execution_lm_profile_id": "lm-exec-1",
                "request_config": {},
                "normalized_config": {},
                "train_inputs": [],
                "val_inputs": [],
            },
        )
        assert missing_create.status_code == 404

        missing_execution = client.post(
            "/optimization/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "strategy": "bootstrap_fewshot",
                "objective": "optimize_demo_quality",
                "request_config": {},
            },
        )
        assert missing_execution.status_code == 400
        assert "execution_lm_profile_id is required" in missing_execution.json()["error"]

        invalid_strategy = client.post(
            "/optimization/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "strategy": "unknown",
                "objective": "optimize_demo_quality",
                "execution_lm_profile_id": "lm-exec-1",
                "request_config": {},
            },
        )
        assert invalid_strategy.status_code == 400
        assert "strategy must be one of" in invalid_strategy.json()["error"]

        invalid_budget = client.post(
            "/optimization/jobs",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "strategy": "miprov2",
                "objective": "optimize_demo_quality",
                "execution_lm_profile_id": "lm-exec-1",
                "request_config": {"budget": "max"},
            },
        )
        assert invalid_budget.status_code == 400
        assert "budget must be one of" in invalid_budget.json()["error"]

        dataset_missing = client.post(
            "/optimization/datasets",
            json={
                "project_id": "proj-1",
                "module_import_id": "missing",
                "name": "Missing dataset",
                "dataset_kind": "feedback",
                "source_type": "eval_feedback",
            },
        )
        assert dataset_missing.status_code == 404

        derived_missing = client.post(
            "/optimization/datasets/derive",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "name": "Missing derive",
                "dataset_kind": "feedback",
                "source_type": "eval_feedback",
                "source_eval_job_ids": ["missing"],
            },
        )
        assert derived_missing.status_code == 404

        assert client.get("/optimization/datasets/missing").status_code == 404
        assert client.get("/optimization/jobs/missing").status_code == 404
        assert client.post("/optimization/jobs/missing/run").status_code == 404
        assert client.post("/optimization/jobs/missing/cancel").status_code == 404
