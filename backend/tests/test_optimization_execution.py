import asyncio
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.executor import module_runner
from app.services import AppServices


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"


class FakeBootstrapFewShot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def compile(self, student, *, teacher=None, trainset):
        assert teacher is None
        assert len(trainset) == 1
        assert trainset[0].inputs().toDict() == {"question": "France capital?"}
        assert trainset[0].answer == "Paris"
        assert trainset[0].label == {"expected": "Paris"}
        student.predict.demos = list(trainset)
        student._compiled = True
        return student


class FakeMIPROv2:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def compile(self, student, *, trainset, valset):
        assert len(trainset) == 1
        assert trainset[0].answer == "Paris"
        assert len(valset) == 1
        assert valset[0].inputs().toDict() == {"question": "France capital?"}
        assert valset[0].label == {"expected": "Paris"}
        student.predict.demos = list(trainset)
        student.candidate_programs = [{"score": 1.0}, {"score": 0.5}]
        student.prompt_model_total_calls = 3
        student.total_calls = 7
        student._compiled = True
        return student


class FakeGEPA:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def compile(self, student, *, trainset, valset):
        assert len(trainset) == 2
        assert len(valset) == 1
        feedback_values = [getattr(example, "optimization_feedback", None) for example in trainset]
        assert feedback_values == ["exact_match", "wrong_answer"]
        student.predict.demos = list(trainset)
        student.detailed_results = SimpleNamespace(
            candidates=["a", "b"],
            total_metric_calls=12,
            num_full_val_evals=4,
        )
        student._compiled = True
        return student


class _FakeConn:
    def __init__(self, state):
        self.state = state

    async def execute(self, query, *params):
        if "set status='running'" in query:
            self.state["job"]["status"] = "running"
            self.state["job"]["run_started_at"] = params[1].isoformat()
        elif "set status='succeeded'" in query:
            self.state["job"]["status"] = "succeeded"
            self.state["job"]["artifact_path"] = params[1]
            self.state["job"]["artifact_metadata"] = json.loads(params[2])
            self.state["job"]["telemetry_summary"] = json.loads(params[3])
            self.state["job"]["comparison_summary"] = json.loads(params[4])
            self.state["job"]["finished_at"] = params[5].isoformat()
        elif "set status='failed'" in query:
            self.state["job"]["status"] = "failed"
            self.state["job"]["failure_reason"] = params[1]
        return "UPDATE 1"


class _FakeAcquire:
    def __init__(self, state):
        self.conn = _FakeConn(state)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        return _FakeAcquire(self.state)


class _PersistentDbConn:
    def __init__(self, state):
        self.state = state

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "insert into optimization_jobs" in normalized:
            self.state["optimization_jobs"][params[0]] = {
                "id": params[0],
                "status": "queued",
                "project_id": params[1],
                "module_import_id": params[2],
                "bundle_path": params[3],
                "strategy": params[4],
                "objective": params[5],
                "dataset_id": params[6],
                "validation_dataset_id": params[7],
                "execution_lm_profile_id": params[8],
                "helper_lm_profile_id": params[9],
                "request_config": params[10],
                "normalized_config": params[11],
                "train_inputs": params[12],
                "val_inputs": params[13],
                "num_threads": params[14],
                "source_eval_job_id": params[15],
                "artifact_path": None,
                "artifact_metadata": "{}",
                "telemetry_summary": "{}",
                "comparison_summary": "{}",
                "failure_reason": None,
                "run_started_at": None,
                "finished_at": None,
                "created_at": params[16],
                "updated_at": params[17],
            }
            return "INSERT 1"

        if "insert into optimization_datasets" in normalized:
            self.state["optimization_datasets"][params[0]] = {
                "id": params[0],
                "project_id": params[1],
                "module_import_id": params[2],
                "name": params[3],
                "dataset_kind": params[4],
                "source_type": params[5],
                "source_eval_job_ids": json.loads(params[6]),
                "source_filters": json.loads(params[7]),
                "records": json.loads(params[8]),
                "record_count": params[9],
                "input_keys": json.loads(params[10]),
                "label_keys": json.loads(params[11]),
                "optimizer_contract": params[12],
                "provenance_summary": json.loads(params[13]),
                "notes": params[14],
                "created_at": params[15],
                "updated_at": params[16],
            }
            return "INSERT 1"

        if "update optimization_jobs set status='running'" in normalized:
            job_id, started_at = params[0], params[1]
            job = self.state["optimization_jobs"].get(str(job_id))
            if job is not None:
                job["status"] = "running"
                job["run_started_at"] = started_at
                job["updated_at"] = started_at
            return "UPDATE 1"

        if "update optimization_jobs set status='succeeded'" in normalized:
            job_id, artifact_path, artifact_metadata, telemetry_summary, comparison_summary, finished_at = params[:6]
            job = self.state["optimization_jobs"].get(str(job_id)) or self.state.get("job")
            if isinstance(job, dict):
                job["status"] = "succeeded"
                job["artifact_path"] = artifact_path
                job["artifact_metadata"] = artifact_metadata if isinstance(artifact_metadata, str) else json.dumps(artifact_metadata)
                job["telemetry_summary"] = telemetry_summary if isinstance(telemetry_summary, str) else json.dumps(telemetry_summary)
                job["comparison_summary"] = comparison_summary if isinstance(comparison_summary, str) else json.dumps(comparison_summary)
                job["failure_reason"] = None
                job["finished_at"] = finished_at
                job["updated_at"] = finished_at
            return "UPDATE 1"

        if "update optimization_jobs set status='failed'" in normalized:
            job_id, reason, finished_at = params[0], params[1], params[2]
            job = self.state["optimization_jobs"].get(str(job_id)) or self.state.get("job")
            if isinstance(job, dict):
                job["status"] = "failed"
                job["failure_reason"] = reason
                job["finished_at"] = finished_at
                job["updated_at"] = finished_at
            return "UPDATE 1"

        if "update optimization_jobs set status='canceled'" in normalized and "returning id" in normalized:
            job_id = params[0]
            job = self.state["optimization_jobs"].get(str(job_id))
            if isinstance(job, dict) and job.get("status") in {"queued", "running"}:
                job["status"] = "canceled"
                job["updated_at"] = params[1]
                return "UPDATE 1"
            return "UPDATE 0"

        return "UPDATE 0"

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())

        if "update optimization_jobs set status='canceled'" in normalized and "returning id" in normalized:
            job_id = str(params[0])
            job = self.state["optimization_jobs"].get(job_id)
            if isinstance(job, dict) and job.get("status") in {"queued", "running"}:
                job["status"] = "canceled"
                job["updated_at"] = params[1]
                return {"id": job_id}
            return None

        if "select id from optimization_jobs" in normalized:
            job = self.state["optimization_jobs"].get(str(params[0]))
            return dict(job) if isinstance(job, dict) else None

        if "from optimization_jobs where id = $1" in normalized:
            job = self.state["optimization_jobs"].get(str(params[0]))
            if isinstance(job, dict):
                return dict(job)
            return None

        if "from optimization_datasets where id = $1" in normalized:
            dataset = self.state["optimization_datasets"].get(str(params[0]))
            if isinstance(dataset, dict):
                return dict(dataset)
            return None

        return None

    async def fetch(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "select id from optimization_jobs" in normalized:
            limit = int(params[0])
            offset = int(params[1])
            ids = sorted(
                self.state["optimization_jobs"].keys(),
                key=lambda job_id: self.state["optimization_jobs"][job_id]["created_at"],
                reverse=True,
            )
            return [{"id": job_id} for job_id in ids[offset : offset + limit]]
        return []

    async def fetchval(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "select 1 from module_imports" in normalized:
            return 1 if params[0] in self.state.get("module_imports", set()) else None
        if "select 1 from lm_profiles" in normalized:
            return 1 if params[0] in self.state.get("lm_profiles", set()) else None
        if "select 1 from optimization_datasets where id = $1" in normalized:
            return 1 if params[0] in self.state.get("optimization_datasets", {}) else None
        return None


class _PersistentDbAcquire:
    def __init__(self, state):
        self.conn = _PersistentDbConn(state)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PersistentDbPool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        return _PersistentDbAcquire(self.state)


def test_run_bundle_optimization_bootstrap_fewshot_saves_artifact_and_demo_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(module_runner.dspy, "BootstrapFewShot", FakeBootstrapFewShot)

    result = module_runner.run_bundle_optimization(
        bundle_path=str(FIXTURES / "valid_bundle"),
        strategy="bootstrap_fewshot",
        train_records=[
            {
                "input": {"question": "France capital?"},
                "label": {"expected": "Paris"},
                "prediction": {"answer": "Paris"},
            }
        ],
        val_inputs=[{"input": {"question": "France capital?"}, "label": {"expected": "Paris"}}],
        artifact_dir=str(tmp_path / "bootstrap-artifact"),
        dspy_config={"max_bootstrapped_demos": 2, "max_labeled_demos": 4},
    )

    assert Path(result["artifact_path"]).exists()
    assert result["artifact_metadata"]["artifact_type"] == "dspy_program_state"
    assert result["telemetry_summary"]["strategy"] == "bootstrap_fewshot"
    assert result["telemetry_summary"]["dataset_summary"]["usable_record_count"] == 1
    assert result["telemetry_summary"]["selected_demos"][0]["demos"][0]["label"] == {"expected": "Paris"}
    assert result["comparison_summary"]["optimized_score_pct"] == 100.0


def test_run_bundle_optimization_miprov2_records_strategy_details(monkeypatch, tmp_path):
    monkeypatch.setattr(module_runner.dspy, "MIPROv2", FakeMIPROv2)

    result = module_runner.run_bundle_optimization(
        bundle_path=str(FIXTURES / "valid_bundle"),
        strategy="miprov2",
        train_records=[
            {
                "input": {"question": "France capital?"},
                "label": {"expected": "Paris"},
                "prediction": {"answer": "Paris"},
            }
        ],
        val_inputs=[{"input": {"question": "France capital?"}, "label": {"expected": "Paris"}}],
        artifact_dir=str(tmp_path / "mipro-artifact"),
        dspy_config={"auto": "light", "max_bootstrapped_demos": 1, "max_labeled_demos": 2},
    )

    assert Path(result["artifact_path"]).exists()
    strategy_details = result["telemetry_summary"]["strategy_details"]
    assert strategy_details["optimizer_class"] == "MIPROv2"
    assert strategy_details["auto"] == "light"
    assert strategy_details["candidate_program_count"] == 2
    assert strategy_details["prompt_model_total_calls"] == 3
    assert strategy_details["total_calls"] == 7


def test_run_bundle_optimization_gepa_includes_feedback(monkeypatch, tmp_path):
    monkeypatch.setattr(module_runner.dspy, "GEPA", FakeGEPA)

    result = module_runner.run_bundle_optimization(
        bundle_path=str(FIXTURES / "valid_bundle"),
        strategy="gepa",
        train_records=[
            {
                "input": {"question": "France capital?"},
                "label": {"expected": "Paris"},
                "prediction": {"answer": "Paris"},
                "feedback": "exact_match",
            },
            {
                "input": {"question": "France capital?"},
                "label": {},
                "prediction": {"answer": "London"},
                "feedback": "wrong_answer",
            },
        ],
        val_inputs=[{"input": {"question": "France capital?"}, "label": {"expected": "Paris"}}],
        artifact_dir=str(tmp_path / "gepa-artifact"),
        num_threads=2,
        dspy_config={"auto": "light", "track_stats": True},
    )

    assert Path(result["artifact_path"]).exists()
    assert result["telemetry_summary"]["strategy"] == "gepa"
    strategy_details = result["telemetry_summary"]["strategy_details"]
    assert strategy_details["optimizer_class"] == "GEPA"
    assert strategy_details["auto"] == "light"
    assert strategy_details["candidate_count"] == 2
    assert strategy_details["total_metric_calls"] == 12
    assert strategy_details["num_full_val_evals"] == 4


def test_run_optimization_job_persists_artifact_and_summaries(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-1",
            "status": "queued",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": "ods-1",
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {"max_bootstrapped_demos": 2, "max_labeled_demos": 4}},
            "train_inputs": [],
            "val_inputs": [{"input": {"question": "France capital?"}, "label": {"expected": "Paris"}}],
            "num_threads": 1,
            "artifact_path": None,
            "artifact_metadata": {},
            "telemetry_summary": {},
            "comparison_summary": {},
            "failure_reason": None,
            "run_started_at": None,
            "finished_at": None,
        }
    }
    setattr(services, "postgres_pool", FakePool(state))

    async def fake_get_optimization_job(job_id):
        assert job_id == "opt-1"
        return dict(state["job"])

    async def fake_get_optimization_dataset(dataset_id):
        assert dataset_id == "ods-1"
        return {
            "id": dataset_id,
            "records": [
                {
                    "input": {"question": "France capital?"},
                    "label": {"expected": "Paris"},
                    "prediction": {"answer": "Paris"},
                }
            ],
        }

    def fake_run_bundle_optimization(**kwargs):
        assert kwargs["strategy"] == "bootstrap_fewshot"
        assert kwargs["train_records"][0]["prediction"] == {"answer": "Paris"}
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-1/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {"strategy": "bootstrap_fewshot", "selected_demos": [{"demo_count": 1}]},
            "comparison_summary": {"baseline_score_pct": 50.0, "optimized_score_pct": 100.0, "score_delta_pct": 50.0},
        }

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "get_optimization_dataset", fake_get_optimization_dataset)
    async def fake_get_lm_profile(lm_profile_id):
        return None

    monkeypatch.setattr(services, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-1"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert result["artifact_path"].endswith("program.json")
    assert result["artifact_metadata"] == {"artifact_type": "dspy_program_state"}
    assert result["telemetry_summary"]["selected_demos"][0]["demo_count"] == 1
    assert result["comparison_summary"]["optimized_score_pct"] == 100.0


def test_run_optimization_job_gepa_calls_bundle_optimization(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-2",
            "status": "queued",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "gepa",
            "dataset_id": "ods-1",
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {"auto": "light", "track_stats": True}},
            "train_inputs": [],
            "val_inputs": [{"input": {"question": "France capital?"}, "label": {"expected": "Paris"}}],
            "num_threads": 1,
        }
    }
    setattr(services, "postgres_pool", FakePool(state))

    async def fake_get_optimization_job(job_id):
        assert job_id == "opt-2"
        return dict(state["job"])

    async def fake_get_optimization_dataset(dataset_id):
        assert dataset_id == "ods-1"
        return {
            "id": dataset_id,
            "records": [
                {
                    "input": {"question": "France capital?"},
                    "label": {"expected": "Paris"},
                    "prediction": {"answer": "Paris"},
                    "feedback": "exact_match",
                }
            ],
        }

    def fake_run_bundle_optimization(**kwargs):
        assert kwargs["strategy"] == "gepa"
        assert len(kwargs["train_records"]) == 1
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-2/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {
                "strategy": "gepa",
                "selected_demos": [{"demo_count": 1}],
                "strategy_details": {"optimizer_class": "GEPA"},
            },
            "comparison_summary": {"baseline_score_pct": 50.0, "optimized_score_pct": 100.0, "score_delta_pct": 50.0},
        }

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "get_optimization_dataset", fake_get_optimization_dataset)

    async def fake_get_lm_profile(lm_profile_id):
        return None

    monkeypatch.setattr(services, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-2"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert result["artifact_path"].endswith("program.json")
    assert result["telemetry_summary"]["strategy"] == "gepa"
    assert result["telemetry_summary"]["strategy_details"]["optimizer_class"] == "GEPA"


def test_optimization_job_json_fields_persist_through_service_db_roundtrip(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    state = {
        "module_imports": {"mod-1"},
        "optimization_jobs": {},
        "optimization_datasets": {
            "ods-1": {
                "id": "ods-1",
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "name": "Passes",
                "dataset_kind": "demo",
                "source_type": "eval_passes",
                "source_eval_job_ids": ["job-1"],
                "source_filters": {"score_threshold": 0.8},
                "records": [
                    {
                        "input": {"question": "France capital?"},
                        "label": {"expected": "Paris"},
                        "prediction": {"answer": "Paris"},
                    }
                ],
                "record_count": 1,
                "input_keys": ["question"],
                "label_keys": ["expected"],
                "optimizer_contract": "dspy_example_v1",
                "provenance_summary": {"included_records": 1},
                "notes": None,
                "created_at": fixed_now,
                "updated_at": fixed_now,
            }
        },
        "lm_profiles": set(),
    }
    setattr(services, "postgres_pool", _PersistentDbPool(state))

    created = asyncio.run(
        services.create_optimization_job(
            project_id="proj-1",
            module_import_id="mod-1",
            bundle_path=str(FIXTURES / "valid_bundle"),
            strategy="gepa",
            objective="optimize_judge_feedback",
            dataset_id="ods-1",
            validation_dataset_id=None,
            execution_lm_profile_id=None,
            helper_lm_profile_id=None,
            request_config={"auto": "light", "track_stats": True},
            normalized_config={"dspy_config": {"auto": "light", "track_stats": True}},
            train_inputs=[],
            val_inputs=[],
            num_threads=1,
            source_eval_job_id=None,
        )
    )
    assert created is not None

    stored_before_run = state["optimization_jobs"][created["id"]]
    assert isinstance(stored_before_run["request_config"], str)
    assert isinstance(stored_before_run["normalized_config"], str)

    def fake_run_bundle_optimization(**kwargs):
        assert kwargs["strategy"] == "gepa"
        assert kwargs["train_records"][0]["label"] == {"expected": "Paris"}
        return {
            "artifact_path": f"/tmp/dspy-trainer/optimization_artifacts/{created['id']}/program.json",
            "artifact_metadata": {
                "artifact_type": "dspy_program_state",
                "artifact_dir": f"/tmp/dspy-trainer/optimization_artifacts/{created['id']}",
                "program_state_path": f"/tmp/dspy-trainer/optimization_artifacts/{created['id']}/program.json",
            },
            "telemetry_summary": {
                "strategy": "gepa",
                "strategy_details": {"optimizer_class": "GEPA", "candidate_count": 3},
            },
            "comparison_summary": {
                "baseline_score_pct": 50.0,
                "optimized_score_pct": 100.0,
                "score_delta_pct": 50.0,
                "baseline_item_count": 1,
                "optimized_item_count": 1,
            },
        }

    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    run_result = asyncio.run(services.run_optimization_job(created["id"]))
    assert run_result is not None
    assert run_result["status"] == "succeeded"

    stored_after_run = state["optimization_jobs"][created["id"]]
    assert isinstance(stored_after_run["artifact_metadata"], str)
    assert isinstance(stored_after_run["telemetry_summary"], str)
    assert isinstance(stored_after_run["comparison_summary"], str)

    fetched = asyncio.run(services.get_optimization_job(created["id"]))
    assert fetched is not None
    assert fetched["artifact_metadata"] == json.loads(stored_after_run["artifact_metadata"])
    assert fetched["telemetry_summary"] == json.loads(stored_after_run["telemetry_summary"])
    assert fetched["comparison_summary"] == json.loads(stored_after_run["comparison_summary"])

    listed = asyncio.run(services.list_optimization_jobs(limit=50, offset=0))
    assert len(listed) == 1
    persisted = listed[0]
    assert persisted["id"] == created["id"]
    assert persisted["artifact_path"] == run_result["artifact_path"]
    assert persisted["artifact_metadata"] == run_result["artifact_metadata"]
    assert persisted["telemetry_summary"] == run_result["telemetry_summary"]
    assert persisted["comparison_summary"] == run_result["comparison_summary"]
