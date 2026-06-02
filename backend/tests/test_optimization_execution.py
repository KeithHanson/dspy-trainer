import asyncio
import json
import sys
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
