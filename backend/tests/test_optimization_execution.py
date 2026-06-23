import asyncio
import json
import sys
from datetime import datetime, timezone
import shutil
import time
from types import SimpleNamespace
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.executor import module_runner
from app.services import AppServices, ModuleSyncError, OptimizationJobCanceled


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"
SAMPLE_BUNDLE = Path(__file__).resolve().parents[1] / "sample_bundles" / "example-bundle"


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


class FakeCountRBootstrapFewShot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def compile(self, student, *, teacher=None, trainset):
        assert teacher is None
        assert len(trainset) == 1
        assert trainset[0].inputs().toDict() == {"message": "RIVER ROAD RR"}
        assert str(trainset[0].r_count) == "5"
        student.count.demos = list(trainset)
        student._compiled = True
        return student


class _FakeConn:
    def __init__(self, state):
        self.state = state

    async def execute(self, query, *params):
        if "set status='running'" in query:
            self.state["job"]["status"] = "running"
            self.state["job"]["execution_log"] = params[2]
            self.state["job"]["run_started_at"] = params[1]
        elif "set status='succeeded'" in query:
            self.state["job"]["status"] = "succeeded"
            self.state["job"]["execution_log"] = params[1]
            self.state["job"]["artifact_path"] = params[2]
            self.state["job"]["artifact_metadata"] = json.loads(params[3])
            self.state["job"]["telemetry_summary"] = json.loads(params[4])
            self.state["job"]["comparison_summary"] = json.loads(params[5])
            self.state["job"]["generated_module_import_id"] = params[6]
            self.state["job"]["optimized_evaluation_plan_id"] = params[7]
            self.state["job"]["optimized_eval_run_plan_id"] = params[8]
            self.state["job"]["resulting_bundle_revision_id"] = params[9]
            self.state["job"]["resulting_bundle_commit_sha"] = params[10]
            self.state["job"]["resulting_bundle_version"] = params[11]
            self.state["job"]["resulting_bundle_branch"] = params[12]
            self.state["job"]["finished_at"] = params[13]
        elif "set status='failed'" in query:
            self.state["job"]["status"] = "failed"
            self.state["job"]["failure_reason"] = params[1]
            self.state["job"]["execution_log"] = params[2]
            self.state["job"]["finished_at"] = params[3]
        elif "set status='canceled'" in query:
            self.state["job"]["status"] = "canceled"
            self.state["job"]["failure_reason"] = params[1]
            self.state["job"]["execution_log"] = params[2]
            self.state["job"]["finished_at"] = params[3]
        return "UPDATE 1"

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "from optimization_jobs where id = $1" in normalized:
            return dict(self.state["job"])
        return None

    async def fetchval(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "select status from optimization_jobs where id = $1" in normalized:
            return self.state["job"].get("status")
        return None

    async def fetch(self, query, *params):
        return []


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


class _RecordingConn:
    def __init__(self):
        self.queries: list[str] = []

    async def execute(self, query, *params):
        del params
        self.queries.append(" ".join(query.strip().split()))
        return "OK"


class _RecordingAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RecordingPool:
    def __init__(self):
        self.conn = _RecordingConn()

    def acquire(self):
        return _RecordingAcquire(self.conn)


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
                "bundle_revision_id": params[4],
                "bundle_commit_sha": params[5],
                "bundle_version": params[6],
                "resulting_bundle_revision_id": None,
                "resulting_bundle_commit_sha": None,
                "resulting_bundle_version": None,
                "resulting_bundle_branch": None,
                "strategy": params[7],
                "objective": params[8],
                "dataset_id": params[9],
                "validation_dataset_id": params[10],
                "execution_lm_profile_id": params[11],
                "helper_lm_profile_id": params[12],
                "request_config": params[13],
                "normalized_config": params[14],
                "train_inputs": params[15],
                "val_inputs": params[16],
                "num_threads": params[17],
                "source_run_plan_id": params[18],
                "generated_module_import_id": None,
                "optimized_evaluation_plan_id": None,
                "optimized_eval_run_plan_id": None,
                "execution_log": params[19],
                "artifact_path": None,
                "artifact_metadata": "{}",
                "telemetry_summary": "{}",
                "comparison_summary": params[20],
                "failure_reason": None,
                "run_started_at": None,
                "finished_at": None,
                "created_at": params[21],
                "updated_at": params[22],
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
                "source_run_plan_ids": json.loads(params[6]),
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
            job_id, started_at, execution_log = params[0], params[1], params[2]
            job = self.state["optimization_jobs"].get(str(job_id))
            if job is not None:
                job["status"] = "running"
                job["execution_log"] = execution_log
                job["run_started_at"] = started_at
                job["updated_at"] = started_at
            return "UPDATE 1"

        if "update optimization_jobs set status='succeeded'" in normalized:
            job_id, execution_log, artifact_path, artifact_metadata, telemetry_summary, comparison_summary, generated_module_import_id, optimized_evaluation_plan_id, optimized_eval_run_plan_id, resulting_bundle_revision_id, resulting_bundle_commit_sha, resulting_bundle_version, resulting_bundle_branch, finished_at = params[:14]
            job = self.state["optimization_jobs"].get(str(job_id)) or self.state.get("job")
            if isinstance(job, dict):
                job["status"] = "succeeded"
                job["execution_log"] = execution_log
                job["artifact_path"] = artifact_path
                job["artifact_metadata"] = artifact_metadata if isinstance(artifact_metadata, str) else json.dumps(artifact_metadata)
                job["telemetry_summary"] = telemetry_summary if isinstance(telemetry_summary, str) else json.dumps(telemetry_summary)
                job["comparison_summary"] = comparison_summary if isinstance(comparison_summary, str) else json.dumps(comparison_summary)
                job["generated_module_import_id"] = generated_module_import_id
                job["optimized_evaluation_plan_id"] = optimized_evaluation_plan_id
                job["optimized_eval_run_plan_id"] = optimized_eval_run_plan_id
                job["resulting_bundle_revision_id"] = resulting_bundle_revision_id
                job["resulting_bundle_commit_sha"] = resulting_bundle_commit_sha
                job["resulting_bundle_version"] = resulting_bundle_version
                job["resulting_bundle_branch"] = resulting_bundle_branch
                job["failure_reason"] = None
                job["finished_at"] = finished_at
                job["updated_at"] = finished_at
            return "UPDATE 1"

        if "update optimization_jobs set status='failed'" in normalized:
            job_id, reason, execution_log, finished_at = params[0], params[1], params[2], params[3]
            job = self.state["optimization_jobs"].get(str(job_id)) or self.state.get("job")
            if isinstance(job, dict):
                job["status"] = "failed"
                job["failure_reason"] = reason
                job["execution_log"] = execution_log
                job["finished_at"] = finished_at
                job["updated_at"] = finished_at
            return "UPDATE 1"

        if "update optimization_jobs set status='canceled', failure_reason=$2" in normalized:
            job_id, reason, execution_log, finished_at = params[0], params[1], params[2], params[3]
            job = self.state["optimization_jobs"].get(str(job_id)) or self.state.get("job")
            if isinstance(job, dict):
                job["status"] = "canceled"
                job["failure_reason"] = reason
                job["execution_log"] = execution_log
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

        if "from agent_run_plans" in normalized and "where id = $1" in normalized:
            plan = self.state.get("agent_run_plans", {}).get(str(params[0]))
            if isinstance(plan, dict):
                return dict(plan)
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
        if "from agent_run_tasks" in normalized and "where plan_id = $1" in normalized:
            plan_id = str(params[0])
            task_rows = list(self.state.get("agent_run_tasks", {}).get(plan_id, []))
            limit = int(params[1])
            offset = int(params[2])
            return task_rows[offset : offset + limit]
        return []

    async def fetchval(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "select 1 from module_imports" in normalized:
            return 1 if params[0] in self.state.get("module_imports", set()) else None
        if "select 1 from lm_profiles" in normalized:
            return 1 if params[0] in self.state.get("lm_profiles", set()) else None
        if "select 1 from optimization_datasets where id = $1" in normalized:
            return 1 if params[0] in self.state.get("optimization_datasets", {}) else None
        if "select 1 from agent_run_plans where id = $1" in normalized:
            return 1 if params[0] in self.state.get("agent_run_plans", {}) else None
        if "select count(*) from agent_run_tasks where plan_id = $1" in normalized:
            return len(self.state.get("agent_run_tasks", {}).get(str(params[0]), []))
        if "select status from optimization_jobs where id = $1" in normalized:
            job = self.state.get("optimization_jobs", {}).get(str(params[0])) or self.state.get("job")
            if isinstance(job, dict):
                return job.get("status")
            return None
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


def test_init_db_creates_tables_before_foreign_key_dependents():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    recording_pool = _RecordingPool()
    setattr(services, "postgres_pool", recording_pool)

    asyncio.run(services.init_db())

    queries = recording_pool.conn.queries
    lm_profiles_idx = next(i for i, query in enumerate(queries) if "create table if not exists lm_profiles" in query)
    bundle_endpoints_idx = next(i for i, query in enumerate(queries) if "create table if not exists bundle_endpoints" in query)
    evaluation_plans_idx = next(i for i, query in enumerate(queries) if "create table if not exists evaluation_plans" in query)
    agent_run_plans_idx = next(i for i, query in enumerate(queries) if "create table if not exists agent_run_plans" in query)
    optimization_jobs_idx = next(i for i, query in enumerate(queries) if "create table if not exists optimization_jobs" in query)

    assert lm_profiles_idx < bundle_endpoints_idx
    assert evaluation_plans_idx < optimization_jobs_idx
    assert agent_run_plans_idx < optimization_jobs_idx


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


def test_run_bundle_optimization_accepts_r_counter_sample_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(module_runner.dspy, "BootstrapFewShot", FakeCountRBootstrapFewShot)

    result = module_runner.run_bundle_optimization(
        bundle_path=str(SAMPLE_BUNDLE),
        strategy="bootstrap_fewshot",
        train_records=[
            {
                "input": {"message": "RIVER ROAD RR"},
                "label": {"expected_r_count": 5},
                "prediction": {"r_count": 5},
            }
        ],
        val_inputs=[{"input": {"message": "RIVER ROAD RR"}, "label": {"expected_r_count": 5}}],
        artifact_dir=str(tmp_path / "r-counter-artifact"),
        execution_lm_profile={
            "model": "dummy",
            "api_base": "http://unused",
            "model_type": "chat",
            "lm_class_path": "dspy.utils.DummyLM",
            "default_params": {"answers": [{"reasoning": "Count the r letters carefully.", "r_count": "5"}]},
        },
        dspy_config={"max_bootstrapped_demos": 2, "max_labeled_demos": 4},
    )

    assert Path(result["artifact_path"]).exists()
    assert result["telemetry_summary"]["strategy"] == "bootstrap_fewshot"
    assert result["artifact_metadata"]["predictor_count"] == 1
    assert result["artifact_metadata"]["selected_demo_count"] >= 0
    assert result["telemetry_summary"]["dataset_summary"]["usable_record_count"] == 1
    assert len(result["telemetry_summary"]["selected_demos"]) == 1


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


def test_run_bundle_optimization_prefers_bundle_target_output_fields(monkeypatch, tmp_path):
    class FakeDeclaredTargetGEPA:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def compile(self, student, *, trainset, valset):
            assert len(trainset) == 1
            assert len(valset) == 1
            train_payload = trainset[0].toDict()
            assert train_payload["response_text"] == "Hello"
            assert train_payload["response_kind"] == "direct_answer"
            assert train_payload["optimization_feedback"] == "keep the direct answer"
            assert "emit_events" not in train_payload
            assert "raw_output" not in train_payload
            student.predict.demos = list(trainset)
            student.detailed_results = SimpleNamespace(
                candidates=["a"],
                total_metric_calls=3,
                num_full_val_evals=1,
            )
            return student

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Sig(dspy.Signature):\n"
        "  question=dspy.InputField()\n"
        "  response_text=dspy.OutputField()\n"
        "  response_kind=dspy.OutputField()\n"
        "  emit_events=dspy.OutputField()\n"
        "  raw_output=dspy.OutputField()\n"
        "class Program(dspy.Module):\n"
        "  def __init__(self):\n"
        "    super().__init__()\n"
        "    self.predict = dspy.Predict(Sig)\n"
        "  def forward(self, question: str):\n"
        "    return self.predict(question=question)\n"
        "def build_program():\n"
        "  return Program()\n",
        encoding="utf-8",
    )
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (bundle / "bundle.toml").write_text(
        "name='declared-targets'\n"
        "version='0.1.0'\n"
        "score_pass_threshold=0.8\n"
        "[optimization]\n"
        "target_output_fields=['response_text', 'response_kind']\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(module_runner.dspy, "GEPA", FakeDeclaredTargetGEPA)

    result = module_runner.run_bundle_optimization(
        bundle_path=str(bundle),
        strategy="gepa",
        train_records=[
            {
                "input": {"question": "Say hello"},
                "label": {"expected_behavior": "reply directly"},
                "prediction": {
                    "response_text": "Hello",
                    "response_kind": "direct_answer",
                    "emit_events": [{"type": "status", "content": "searching"}],
                    "raw_output": {"trace": "internal"},
                },
                "feedback": "keep the direct answer",
            }
        ],
        val_inputs=[{"input": {"question": "Say hello"}, "label": {"expected_behavior": "reply directly"}}],
        artifact_dir=str(tmp_path / "declared-target-artifact"),
        dspy_config={"auto": "light", "track_stats": True},
    )

    assert Path(result["artifact_path"]).exists()
    assert result["telemetry_summary"]["dataset_summary"]["target_provenance_counts"] == {"prediction_payload": 1}
    assert result["telemetry_summary"]["strategy_details"]["optimizer_class"] == "GEPA"


def test_run_optimization_job_persists_artifact_and_summaries(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-1",
            "status": "queued",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
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
            "source_run_plan_id": "plan-1",
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

    async def fake_get_source_run_plan_baseline(**kwargs):
        assert kwargs == {
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "source_run_plan_id": "plan-1",
        }
        return None

    def fake_run_bundle_optimization(**kwargs):
        assert kwargs["strategy"] == "bootstrap_fewshot"
        assert kwargs["train_records"][0]["prediction"] == {"answer": "Paris"}
        kwargs["log_event"]("bootstrap raw stdout line")
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-1/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {"strategy": "bootstrap_fewshot", "selected_demos": [{"demo_count": 1}]},
        }

    async def fake_apply_writeback(job_payload, *, bundle_name=None, bundle_version=None):
        del bundle_name, bundle_version
        assert job_payload["artifact_path"].endswith("/opt-1/program.json")
        return {
            "module_id": "mod-opt-1",
            "source_root": str(FIXTURES / "valid_bundle"),
            "optimized_bundle_name": "Echo",
            "optimized_bundle_version": "0.1.0",
            "report": type("Report", (), {"passed": True, "diagnostics": [], "metadata": {"name": "Echo", "version": "0.1.0"}})(),
            "expected_branch": "main",
        }

    async def fake_materialize_from_job(job_payload, *, bundle_name=None, bundle_version=None, commit_message=None):
        del job_payload, bundle_name, bundle_version, commit_message
        return {
            "id": "mod-opt-1",
            "resulting_bundle_revision_id": "rev-opt-1",
            "resulting_bundle_commit_sha": "commit-opt-1",
            "resulting_bundle_version": "0.1.0",
            "resulting_bundle_branch": "optimization-opt",
        }
    async def fake_get_module(module_id):
        if module_id == "mod-opt-1":
            return {"id": module_id, "source_ref": str(FIXTURES / "valid_bundle")}
        return None
    async def fake_create_followup_eval_plan_and_run(**kwargs):
        assert kwargs["source_run_plan_id"] == "plan-1"
        return {"id": "eval-opt-1"}, {"id": "run-opt-1"}
    async def fake_enqueue_agent_run_plan(plan_id):
        assert plan_id == "run-opt-1"
        return {"id": plan_id}
    async def fake_await_agent_run_plan_completion(plan_id, timeout_s=600.0):
        assert plan_id == "run-opt-1"
        return {"id": plan_id, "status": "succeeded"}
    async def fake_get_agent_run_plan_score_summary(plan_id):
        assert plan_id == "run-opt-1"
        return {"average_score_pct": 100.0, "item_count": 1}

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "get_optimization_dataset", fake_get_optimization_dataset)
    monkeypatch.setattr(services, "_get_source_run_plan_baseline", fake_get_source_run_plan_baseline)
    async def fake_append_optimization_process_log(job_id, additions):
        return None
    async def fake_get_lm_profile(lm_profile_id):
        return None
    requirement_installs: list[str] = []

    async def fake_ensure_bundle_requirements_installed(bundle_path, cancel_check=None):
        requirement_installs.append(bundle_path)

    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)
    monkeypatch.setattr(services, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr(services, "ensure_bundle_requirements_installed", fake_ensure_bundle_requirements_installed)
    monkeypatch.setattr(services, "_apply_optimized_bundle_writeback", fake_apply_writeback)
    monkeypatch.setattr(services, "_materialize_optimized_bundle_from_job", fake_materialize_from_job)
    monkeypatch.setattr(services, "get_module", fake_get_module)
    monkeypatch.setattr(services, "_create_followup_eval_plan_and_run", fake_create_followup_eval_plan_and_run)
    monkeypatch.setattr(services, "enqueue_agent_run_plan", fake_enqueue_agent_run_plan)
    monkeypatch.setattr(services, "_await_agent_run_plan_completion", fake_await_agent_run_plan_completion)
    monkeypatch.setattr(services, "_get_agent_run_plan_score_summary", fake_get_agent_run_plan_score_summary)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-1"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert result["generated_module_import_id"] == "mod-opt-1"
    assert "status=succeeded" in result["execution_log"]
    assert "generated_module_import_id=mod-opt-1" in result["execution_log"]
    assert "bootstrap raw stdout line" in result["execution_log"]
    assert "artifact_path=/tmp/dspy-trainer/optimization_artifacts/opt-1/program.json" in result["execution_log"]
    assert result["artifact_path"].endswith("program.json")
    assert result["artifact_metadata"] == {"artifact_type": "dspy_program_state"}
    assert result["telemetry_summary"]["selected_demos"][0]["demo_count"] == 1
    assert requirement_installs == [str(FIXTURES / "valid_bundle")]
    assert result["comparison_summary"] == {
        "baseline_score_pct": None,
        "optimized_score_pct": 100.0,
        "score_delta_pct": None,
        "baseline_item_count": None,
        "optimized_item_count": 1,
    }


def test_run_optimization_job_gepa_calls_bundle_optimization(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-2",
            "status": "queued",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
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
            "source_run_plan_id": "plan-2",
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

    async def fake_get_source_run_plan_baseline(**kwargs):
        assert kwargs == {
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "source_run_plan_id": "plan-2",
        }
        return None

    def fake_run_bundle_optimization(**kwargs):
        assert kwargs["strategy"] == "gepa"
        assert len(kwargs["train_records"]) == 1
        kwargs["log_event"]("gepa raw log line")
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-2/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {
                "strategy": "gepa",
                "selected_demos": [{"demo_count": 1}],
                "strategy_details": {"optimizer_class": "GEPA"},
            },
        }

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "get_optimization_dataset", fake_get_optimization_dataset)
    monkeypatch.setattr(services, "_get_source_run_plan_baseline", fake_get_source_run_plan_baseline)
    async def fake_append_optimization_process_log(job_id, additions):
        return None

    async def fake_get_lm_profile(lm_profile_id):
        return None

    async def fake_apply_writeback(job_payload, *, bundle_name=None, bundle_version=None):
        del job_payload, bundle_name, bundle_version
        return {
            "module_id": "mod-opt-2",
            "source_root": str(FIXTURES / "valid_bundle"),
            "optimized_bundle_name": "Echo",
            "optimized_bundle_version": "0.1.0",
            "report": type("Report", (), {"passed": True, "diagnostics": [], "metadata": {"name": "Echo", "version": "0.1.0"}})(),
            "expected_branch": "main",
        }

    async def fake_materialize_from_job(job_payload, *, bundle_name=None, bundle_version=None, commit_message=None):
        del job_payload, bundle_name, bundle_version, commit_message
        return {
            "id": "mod-opt-2",
            "resulting_bundle_revision_id": "rev-opt-2",
            "resulting_bundle_commit_sha": "commit-opt-2",
            "resulting_bundle_version": "0.1.0",
            "resulting_bundle_branch": "optimization-opt",
        }
    async def fake_get_module(module_id):
        if module_id == "mod-opt-2":
            return {"id": module_id, "source_ref": str(FIXTURES / "valid_bundle")}
        return None
    async def fake_create_followup_eval_plan_and_run(**kwargs):
        assert kwargs["source_run_plan_id"] == "plan-2"
        return {"id": "eval-opt-2"}, {"id": "run-opt-2"}
    async def fake_enqueue_agent_run_plan(plan_id):
        assert plan_id == "run-opt-2"
        return {"id": plan_id}
    async def fake_await_agent_run_plan_completion(plan_id, timeout_s=600.0):
        assert plan_id == "run-opt-2"
        return {"id": plan_id, "status": "succeeded"}
    async def fake_get_agent_run_plan_score_summary(plan_id):
        assert plan_id == "run-opt-2"
        return {"average_score_pct": 95.0, "item_count": 1}

    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)
    monkeypatch.setattr(services, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr(services, "_apply_optimized_bundle_writeback", fake_apply_writeback)
    monkeypatch.setattr(services, "_materialize_optimized_bundle_from_job", fake_materialize_from_job)
    monkeypatch.setattr(services, "get_module", fake_get_module)
    monkeypatch.setattr(services, "_create_followup_eval_plan_and_run", fake_create_followup_eval_plan_and_run)
    monkeypatch.setattr(services, "enqueue_agent_run_plan", fake_enqueue_agent_run_plan)
    monkeypatch.setattr(services, "_await_agent_run_plan_completion", fake_await_agent_run_plan_completion)
    monkeypatch.setattr(services, "_get_agent_run_plan_score_summary", fake_get_agent_run_plan_score_summary)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-2"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert "normalized_strategy=gepa" in result["execution_log"]
    assert "gepa raw log line" in result["execution_log"]
    assert result["artifact_path"].endswith("program.json")
    assert result["telemetry_summary"]["strategy"] == "gepa"
    assert result["telemetry_summary"]["strategy_details"]["optimizer_class"] == "GEPA"


def test_run_optimization_job_derives_records_from_source_run_plan(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-derive",
            "status": "queued",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": None,
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dataset_requirements": {"dataset_kind": "demo"}, "dspy_config": {"max_bootstrapped_demos": 2}},
            "train_inputs": [],
            "val_inputs": [],
            "num_threads": 1,
            "source_run_plan_id": "plan-1",
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
        assert job_id == "opt-derive"
        return dict(state["job"])

    async def fake_derive_optimization_dataset(**kwargs):
        assert kwargs["source_run_plan_ids"] == ["plan-1"]
        assert kwargs["dataset_kind"] == "demo"
        assert kwargs["source_type"] == "eval_passes"
        return {
            "records": [
                {
                    "input": {"question": "France capital?"},
                    "label": {"expected": "Paris"},
                    "prediction": {"answer": "Paris"},
                }
            ]
        }

    def fake_run_bundle_optimization(**kwargs):
        assert kwargs["train_records"][0]["label"] == {"expected": "Paris"}
        assert kwargs["train_records"][0]["prediction"] == {"answer": "Paris"}
        assert kwargs["baseline_summary"] == {"score_pct": 90.0, "item_count": 2}
        kwargs["log_event"]("derived dataset raw line")
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-derive/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {"strategy": "bootstrap_fewshot"},
        }

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "derive_optimization_dataset", fake_derive_optimization_dataset)
    async def fake_get_source_run_plan_baseline(**kwargs):
        assert kwargs == {
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "source_run_plan_id": "plan-1",
        }
        return {"score_pct": 90.0, "item_count": 2}
    async def fake_apply_writeback(job_payload, *, bundle_name=None, bundle_version=None):
        del job_payload, bundle_name, bundle_version
        return {
            "module_id": "mod-opt-derive",
            "source_root": str(FIXTURES / "valid_bundle"),
            "optimized_bundle_name": "Echo",
            "optimized_bundle_version": "0.1.0",
            "report": type("Report", (), {"passed": True, "diagnostics": [], "metadata": {"name": "Echo", "version": "0.1.0"}})(),
            "expected_branch": "main",
        }

    async def fake_materialize_from_job(job_payload, *, bundle_name=None, bundle_version=None, commit_message=None):
        del job_payload, bundle_name, bundle_version, commit_message
        return {
            "id": "mod-opt-derive",
            "resulting_bundle_revision_id": "rev-opt-derive",
            "resulting_bundle_commit_sha": "commit-opt-derive",
            "resulting_bundle_version": "0.1.0",
            "resulting_bundle_branch": "optimization-opt",
        }
    async def fake_create_followup_eval_plan_and_run(**kwargs):
        assert kwargs["source_run_plan_id"] == "plan-1"
        assert kwargs["module_import_id"] == "mod-opt-derive"
        assert kwargs["bundle_path"]
        return {"id": "eval-opt-derive"}, {"id": "run-opt-derive"}
    async def fake_enqueue_agent_run_plan(plan_id):
        assert plan_id == "run-opt-derive"
        return {"id": plan_id}
    async def fake_await_agent_run_plan_completion(plan_id, timeout_s=600.0):
        assert plan_id == "run-opt-derive"
        return {"id": plan_id, "status": "succeeded"}
    async def fake_get_agent_run_plan_score_summary(plan_id):
        assert plan_id == "run-opt-derive"
        return {"average_score_pct": 88.0, "item_count": 6}
    async def fake_get_module(module_id):
        if module_id == "mod-opt-derive":
            return {"id": module_id, "source_ref": str(FIXTURES / "valid_bundle")}
        return None
    monkeypatch.setattr(services, "_get_source_run_plan_baseline", fake_get_source_run_plan_baseline)
    async def fake_append_optimization_process_log(job_id, additions):
        return None
    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)
    monkeypatch.setattr(services, "_apply_optimized_bundle_writeback", fake_apply_writeback)
    monkeypatch.setattr(services, "_materialize_optimized_bundle_from_job", fake_materialize_from_job)
    monkeypatch.setattr(services, "_create_followup_eval_plan_and_run", fake_create_followup_eval_plan_and_run)
    monkeypatch.setattr(services, "enqueue_agent_run_plan", fake_enqueue_agent_run_plan)
    monkeypatch.setattr(services, "_await_agent_run_plan_completion", fake_await_agent_run_plan_completion)
    monkeypatch.setattr(services, "_get_agent_run_plan_score_summary", fake_get_agent_run_plan_score_summary)
    monkeypatch.setattr(services, "get_module", fake_get_module)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-derive"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert result["generated_module_import_id"] == "mod-opt-derive"
    assert result["optimized_evaluation_plan_id"] == "eval-opt-derive"
    assert result["optimized_eval_run_plan_id"] == "run-opt-derive"
    assert "optimized_evaluation_plan_id=eval-opt-derive" in result["execution_log"]
    assert "derived_source_run_plan_id=plan-1" in result["execution_log"]
    assert "baseline_source_run_plan_id=plan-1" in result["execution_log"]
    assert "optimized_eval_run_plan_id=run-opt-derive" in result["execution_log"]
    assert "derived dataset raw line" in result["execution_log"]
    assert result["comparison_summary"] == {
        "baseline_score_pct": 90.0,
        "optimized_score_pct": 88.0,
        "score_delta_pct": -2.0,
        "baseline_item_count": 2,
        "optimized_item_count": 6,
    }


def test_run_optimization_job_persists_traceback_on_failure(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-fail",
            "status": "queued",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": None,
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {}},
            "train_inputs": [
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}, "prediction": {"answer": "Paris"}}
            ],
            "val_inputs": [],
            "num_threads": 1,
            "source_run_plan_id": "plan-fail",
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
        assert job_id == "opt-fail"
        return dict(state["job"])

    async def fake_get_source_run_plan_baseline(**kwargs):
        assert kwargs == {
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "source_run_plan_id": "plan-fail",
        }
        return None

    def fake_run_bundle_optimization(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "_get_source_run_plan_baseline", fake_get_source_run_plan_baseline)
    async def fake_append_optimization_process_log(job_id, additions):
        return None
    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-fail"))

    assert result is not None
    assert result["status"] == "failed"
    assert "traceback_begin" in result["execution_log"]
    assert "RuntimeError: boom" in result["execution_log"]
    assert "traceback_end" in result["execution_log"]


def test_run_optimization_job_flushes_live_log_updates(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-live",
            "status": "queued",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": None,
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {}},
            "train_inputs": [
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}, "prediction": {"answer": "Paris"}}
            ],
            "val_inputs": [],
            "num_threads": 1,
            "source_run_plan_id": "plan-live",
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

    appended_batches: list[list[str]] = []

    async def fake_get_optimization_job(job_id):
        assert job_id == "opt-live"
        return dict(state["job"])

    async def fake_get_source_run_plan_baseline(**kwargs):
        assert kwargs == {
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "source_run_plan_id": "plan-live",
        }
        return None

    async def fake_append_optimization_process_log(job_id, additions):
        assert job_id == "opt-live"
        appended_batches.append(list(additions))

    def fake_run_bundle_optimization(**kwargs):
        kwargs["log_event"]("live-line-1")
        time.sleep(0.35)
        kwargs["log_event"]("live-line-2")
        time.sleep(0.35)
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-live/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {"strategy": "bootstrap_fewshot"},
        }

    async def fake_apply_writeback(job_payload, *, bundle_name=None, bundle_version=None):
        del job_payload, bundle_name, bundle_version
        return {
            "module_id": "mod-opt-live",
            "source_root": str(FIXTURES / "valid_bundle"),
            "optimized_bundle_name": "Echo",
            "optimized_bundle_version": "0.1.0",
            "report": type("Report", (), {"passed": True, "diagnostics": [], "metadata": {"name": "Echo", "version": "0.1.0"}})(),
            "expected_branch": "main",
        }

    async def fake_materialize_from_job(job_payload, *, bundle_name=None, bundle_version=None, commit_message=None):
        del job_payload, bundle_name, bundle_version, commit_message
        return {"id": "mod-opt-live", "current_revision_id": "rev-opt-live", "current_commit_sha": "commit-opt-live", "bundle_version": "0.1.0"}
    async def fake_get_module(module_id):
        if module_id == "mod-opt-live":
            return {"id": module_id, "source_ref": str(FIXTURES / "valid_bundle")}
        return None
    async def fake_create_followup_eval_plan_and_run(**kwargs):
        assert kwargs["source_run_plan_id"] == "plan-live"
        return {"id": "eval-opt-live"}, {"id": "run-opt-live"}
    async def fake_enqueue_agent_run_plan(plan_id):
        assert plan_id == "run-opt-live"
        return {"id": plan_id}
    async def fake_await_agent_run_plan_completion(plan_id, timeout_s=600.0):
        assert plan_id == "run-opt-live"
        return {"id": plan_id, "status": "succeeded"}
    async def fake_get_agent_run_plan_score_summary(plan_id):
        assert plan_id == "run-opt-live"
        return {"average_score_pct": 100.0, "item_count": 1}

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "_get_source_run_plan_baseline", fake_get_source_run_plan_baseline)
    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)
    monkeypatch.setattr(services, "_apply_optimized_bundle_writeback", fake_apply_writeback)
    monkeypatch.setattr(services, "_materialize_optimized_bundle_from_job", fake_materialize_from_job)
    monkeypatch.setattr(services, "get_module", fake_get_module)
    monkeypatch.setattr(services, "_create_followup_eval_plan_and_run", fake_create_followup_eval_plan_and_run)
    monkeypatch.setattr(services, "enqueue_agent_run_plan", fake_enqueue_agent_run_plan)
    monkeypatch.setattr(services, "_await_agent_run_plan_completion", fake_await_agent_run_plan_completion)
    monkeypatch.setattr(services, "_get_agent_run_plan_score_summary", fake_get_agent_run_plan_score_summary)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-live"))

    assert result is not None
    assert result["status"] == "succeeded"
    assert any("live-line-1" in batch for batch in appended_batches)
    assert any("live-line-2" in batch for batch in appended_batches)


def test_run_optimization_job_marks_canceled_when_subprocess_is_terminated(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-cancel",
            "status": "running",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": None,
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {}},
            "train_inputs": [
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}, "prediction": {"answer": "Paris"}}
            ],
            "val_inputs": [],
            "num_threads": 1,
            "source_run_plan_id": "plan-cancel",
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
        assert job_id == "opt-cancel"
        return dict(state["job"])

    async def fake_run_optimization_in_subprocess(*args, **kwargs):
        raise OptimizationJobCanceled("optimization job canceled by operator")

    async def fake_append_optimization_process_log(job_id, additions):
        return None

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "_run_optimization_in_subprocess", fake_run_optimization_in_subprocess)
    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)

    result = asyncio.run(services.run_optimization_job("opt-cancel"))

    assert result is not None
    assert result["status"] == "canceled"
    assert "status=canceled" in result["execution_log"]
    assert result["failure_reason"] == "optimization job canceled by operator"


def test_run_optimization_job_marks_canceled_when_requirements_install_sees_cancel(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-cancel-install",
            "status": "running",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": None,
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {}},
            "train_inputs": [
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}, "prediction": {"answer": "Paris"}}
            ],
            "val_inputs": [],
            "num_threads": 1,
            "source_run_plan_id": "plan-cancel-install",
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
        assert job_id == "opt-cancel-install"
        return dict(state["job"])

    async def fake_ensure_bundle_requirements_installed(bundle_path, cancel_check=None):
        assert cancel_check is not None
        state["job"]["status"] = "canceled"
        if await cancel_check():
            raise RuntimeError("eval run canceled by operator")

    async def fake_append_optimization_process_log(job_id, additions):
        return None

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "ensure_bundle_requirements_installed", fake_ensure_bundle_requirements_installed)
    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)

    result = asyncio.run(services.run_optimization_job("opt-cancel-install"))

    assert result is not None
    assert result["status"] == "canceled"
    assert "status=canceled" in result["execution_log"]
    assert result["failure_reason"] == "optimization job canceled by operator"


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
                "source_run_plan_ids": ["plan-1"],
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
        "agent_run_plans": {
            "plan-1": {
                "id": "plan-1",
                "status": "succeeded",
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "plan_name": "Baseline plan",
                "lm_profile_id": None,
                "bundle_path": str(FIXTURES / "valid_bundle"),
                "eval_inputs": [],
                "mlflow_experiment_id": None,
                "mlflow_parent_run_id": None,
                "runs_per_question": 1,
                "max_workers": 1,
                "total_tasks": 2,
                "completed_tasks": 2,
                "failed_tasks": 0,
                "failure_reason": None,
                "created_at": fixed_now,
                "updated_at": fixed_now,
            }
        },
        "agent_run_tasks": {
            "plan-1": [
                {
                    "id": "task-1",
                    "plan_id": "plan-1",
                    "status": "succeeded",
                    "question_index": 0,
                    "attempt_index": 0,
                    "input_payload": {"question": "France capital?"},
                    "label_payload": {"expected": "Paris"},
                    "prediction_payload": {"answer": "Paris"},
                    "score": 0.5,
                    "eval_pass": False,
                    "rationale": "partial",
                    "error": None,
                    "worker_log": "",
                    "worker_id": "worker-1",
                    "created_at": fixed_now,
                    "updated_at": fixed_now,
                },
                {
                    "id": "task-2",
                    "plan_id": "plan-1",
                    "status": "succeeded",
                    "question_index": 1,
                    "attempt_index": 0,
                    "input_payload": {"question": "Spain capital?"},
                    "label_payload": {"expected": "Madrid"},
                    "prediction_payload": {"answer": "Madrid"},
                    "score": 1.0,
                    "eval_pass": True,
                    "rationale": "exact_match",
                    "error": None,
                    "worker_log": "",
                    "worker_id": "worker-1",
                    "created_at": fixed_now,
                    "updated_at": fixed_now,
                },
            ]
        },
        "lm_profiles": set(),
    }
    setattr(services, "postgres_pool", _PersistentDbPool(state))

    async def fake_resolve_module_execution_state(module_import_id, fallback_bundle_path=None):
        assert module_import_id == "mod-1"
        return {
            "module_id": module_import_id,
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "bundle_revision_id": "rev-1",
            "bundle_commit_sha": "abc123",
            "bundle_version": "0.1.0",
            "bundle_name": "valid-bundle",
        }

    services.resolve_module_execution_state = fake_resolve_module_execution_state  # type: ignore[method-assign]

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
            source_run_plan_id="plan-1",
        )
    )
    assert created is not None
    assert created["comparison_summary"] == {
        "baseline_score_pct": 75.0,
        "optimized_score_pct": None,
        "score_delta_pct": None,
        "baseline_item_count": 2,
        "optimized_item_count": None,
    }

    stored_before_run = state["optimization_jobs"][created["id"]]
    assert isinstance(stored_before_run["request_config"], str)
    assert isinstance(stored_before_run["normalized_config"], str)
    assert json.loads(stored_before_run["comparison_summary"])["baseline_score_pct"] == 75.0
    assert "baseline_source_run_plan_id=plan-1" in stored_before_run["execution_log"]

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
        }

    async def fake_apply_writeback(job_payload, *, bundle_name=None, bundle_version=None):
        del bundle_name, bundle_version
        assert job_payload["artifact_path"].endswith(f"/{created['id']}/program.json")
        return {
            "module_id": "mod-opt-roundtrip",
            "source_root": str(FIXTURES / "valid_bundle"),
            "optimized_bundle_name": "valid-bundle",
            "optimized_bundle_version": "2.0.0",
            "report": type("Report", (), {"passed": True, "diagnostics": [], "metadata": {"name": "valid-bundle", "version": "2.0.0"}})(),
            "expected_branch": "main",
        }

    async def fake_materialize_from_job(job_payload, *, bundle_name=None, bundle_version=None, commit_message=None):
        del bundle_name, bundle_version
        assert job_payload["artifact_path"].endswith(f"/{created['id']}/program.json")
        assert commit_message and "baseline 75.0% -> optimized 91.0%" in commit_message
        return {
            "id": "mod-opt-roundtrip",
            "resulting_bundle_revision_id": "rev-opt-roundtrip",
            "resulting_bundle_commit_sha": "commit-opt-roundtrip",
            "resulting_bundle_version": "2.0.0",
            "resulting_bundle_branch": "optimization-opt",
        }

    async def fake_create_followup_eval_plan_and_run(**kwargs):
        assert kwargs["source_run_plan_id"] == "plan-1"
        assert kwargs["module_import_id"] == "mod-opt-roundtrip"
        return {"id": "eval-opt-roundtrip"}, {"id": "run-opt-roundtrip"}

    async def fake_enqueue_agent_run_plan(plan_id):
        assert plan_id == "run-opt-roundtrip"
        return {"id": plan_id}

    async def fake_await_agent_run_plan_completion(plan_id, timeout_s=600.0):
        assert plan_id == "run-opt-roundtrip"
        return {"id": plan_id, "status": "succeeded"}

    async def fake_get_agent_run_plan_score_summary(plan_id):
        assert plan_id == "run-opt-roundtrip"
        return {"average_score_pct": 91.0, "item_count": 2}

    async def fake_get_module(module_id):
        if module_id == "mod-opt-roundtrip":
            return {"id": module_id, "source_ref": str(FIXTURES / "valid_bundle")}
        return None

    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)
    monkeypatch.setattr(services, "_apply_optimized_bundle_writeback", fake_apply_writeback)
    monkeypatch.setattr(services, "_materialize_optimized_bundle_from_job", fake_materialize_from_job)
    monkeypatch.setattr(services, "_create_followup_eval_plan_and_run", fake_create_followup_eval_plan_and_run)
    monkeypatch.setattr(services, "enqueue_agent_run_plan", fake_enqueue_agent_run_plan)
    monkeypatch.setattr(services, "_await_agent_run_plan_completion", fake_await_agent_run_plan_completion)
    monkeypatch.setattr(services, "_get_agent_run_plan_score_summary", fake_get_agent_run_plan_score_summary)
    monkeypatch.setattr(services, "get_module", fake_get_module)

    run_result = asyncio.run(services.run_optimization_job(created["id"]))
    assert run_result is not None
    assert run_result["status"] == "succeeded"
    assert run_result["generated_module_import_id"] == "mod-opt-roundtrip"
    assert run_result["resulting_bundle_revision_id"] == "rev-opt-roundtrip"
    assert run_result["resulting_bundle_commit_sha"] == "commit-opt-roundtrip"
    assert run_result["resulting_bundle_version"] == "2.0.0"
    assert run_result["optimized_evaluation_plan_id"] == "eval-opt-roundtrip"
    assert run_result["optimized_eval_run_plan_id"] == "run-opt-roundtrip"
    assert run_result["comparison_summary"] == {
        "baseline_score_pct": 75.0,
        "optimized_score_pct": 91.0,
        "score_delta_pct": 16.0,
        "baseline_item_count": 2,
        "optimized_item_count": 2,
    }

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
    assert persisted["resulting_bundle_commit_sha"] == "commit-opt-roundtrip"


def test_materialize_optimized_bundle_updates_existing_checkout(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

    repo_root = tmp_path / "repo"
    source_bundle = repo_root / "bundles" / "source-bundle"
    source_bundle.mkdir(parents=True)
    (source_bundle / "module.py").write_text(
        "import dspy\n"
        "class Sig(dspy.Signature):\n"
        "  q=dspy.InputField()\n"
        "  a=dspy.OutputField()\n"
        "class Agent(dspy.Module):\n"
        "  def forward(self, q: str):\n"
        "    return dspy.Prediction(a='x')\n"
        "def build_program():\n"
        "  return Agent()\n",
        encoding="utf-8",
    )
    (source_bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (source_bundle / "bundle.toml").write_text(
        "name='echo-bundle'\nversion='0.1.0'\nscore_pass_threshold=0.8\n",
        encoding="utf-8",
    )

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    artifact_path = artifact_dir / "program.json"
    artifact_path.write_text('{"answer": "Paris"}', encoding="utf-8")

    git_calls: list[tuple[list[str], str | None]] = []
    branch_root = tmp_path / "worktree"

    async def fake_get_optimization_job(job_id):
        assert job_id == "opt-123"
        return {
            "id": "opt-123",
            "status": "succeeded",
            "module_import_id": "mod-1",
            "artifact_path": str(artifact_path),
        }

    async def fake_get_module(module_id):
        if module_id == "mod-1":
            return {
                "id": "mod-1",
                "source": "github",
                "bundle_name": "echo-bundle",
                "bundle_version": "0.1.0",
                "source_ref": str(source_bundle),
                "checkout_path": str(repo_root),
                "github_branch": "main",
                "github_subpath": "bundles/source-bundle",
            }
        return None

    async def fake_create_worktree(repo_root_arg, *, base_branch, optimization_job_id):
        assert repo_root_arg == repo_root
        assert base_branch == "main"
        assert optimization_job_id == "opt-123"
        shutil.copytree(repo_root, branch_root)
        return branch_root, "optimization-opt"

    async def fake_remove_worktree(repo_root_arg, worktree_path):
        assert repo_root_arg == repo_root
        assert worktree_path == branch_root

    async def fake_create_noncurrent_bundle_revision(module_id, **kwargs):
        assert module_id == "mod-1"
        assert kwargs["commit_sha"] == "commit-after"
        assert kwargs["bundle_name"] == "custom-bundle"
        assert kwargs["bundle_version"] == "2.0.0"
        assert kwargs["source_event"] == "optimization_branch"
        return "rev-after"

    async def fake_run_git_command(args, *, cwd=None):
        git_calls.append((args, str(cwd) if cwd is not None else None))
        if args[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            if str(cwd) == str(branch_root):
                return "optimization-opt"
            return "main"
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return "commit-after"
        return ""

    async def fake_get_updated_module(module_id):
        if module_id != "mod-1":
            return None
        return {
            "id": "mod-1",
            "source": "github",
            "source_ref": str(source_bundle),
            "checkout_path": str(repo_root),
            "github_branch": "main",
            "github_subpath": "bundles/source-bundle",
            "bundle_name": "echo-bundle",
            "bundle_version": "0.1.0",
            "validation_status": "passed",
            "smoke_status": "pending",
            "diagnostics": [],
            "current_revision_id": "rev-before",
        }

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "get_module", fake_get_updated_module)
    monkeypatch.setattr(services, "_create_optimization_worktree", fake_create_worktree)
    monkeypatch.setattr(services, "_remove_optimization_worktree", fake_remove_worktree)
    monkeypatch.setattr(services, "_create_noncurrent_bundle_revision", fake_create_noncurrent_bundle_revision)
    monkeypatch.setattr(services, "_run_git_command", fake_run_git_command)
    async def fake_ensure_module_mutation_allowed(module_id):
        assert module_id == "mod-1"
        return {"sync_status": "synced"}

    monkeypatch.setattr(services, "ensure_module_mutation_allowed", fake_ensure_module_mutation_allowed)

    result = asyncio.run(services.materialize_optimized_bundle("opt-123", bundle_name="custom-bundle", bundle_version="2.0.0"))

    assert result is not None
    assert result["bundle_name"] == "echo-bundle"
    assert result["resulting_bundle_branch"] == "optimization-opt"
    assert result["resulting_bundle_revision_id"] == "rev-after"
    assert result["resulting_bundle_commit_sha"] == "commit-after"
    assert result["resulting_bundle_version"] == "2.0.0"
    materialized_root = Path(branch_root) / "bundles" / "source-bundle"
    assert materialized_root.joinpath("program.json").exists()
    bundle_toml = materialized_root.joinpath("bundle.toml").read_text(encoding="utf-8")
    assert 'name = "custom-bundle"' in bundle_toml
    assert 'version = "2.0.0"' in bundle_toml
    assert 'optimized_program_state = "program.json"' in bundle_toml
    assert 'source_optimization_job_id = "opt-123"' in bundle_toml
    assert any(call[0] == ["git", "add", "."] for call in git_calls)
    assert not any(call[0] == ["git", "add", "bundle.toml"] for call in git_calls)
    assert any(call[0][0] == "git" and "commit" in call[0] and "-m" in call[0] for call in git_calls)
    assert any(call[0][:4] == ["git", "push", "origin", "optimization-opt"] for call in git_calls)


def test_materialize_optimized_bundle_strips_legacy_generated_suffix_from_name(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

    repo_root = tmp_path / "repo-legacy"
    source_bundle = repo_root / "bundles" / "source-bundle"
    source_bundle.mkdir(parents=True)
    (source_bundle / "module.py").write_text(
        "import dspy\nclass Sig(dspy.Signature):\n  q=dspy.InputField()\n  a=dspy.OutputField()\nclass Agent(dspy.Module):\n  def forward(self, q: str):\n    return dspy.Prediction(a='x')\ndef build_program():\n  return Agent()\n",
        encoding="utf-8",
    )
    (source_bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (source_bundle / "bundle.toml").write_text(
        "name='support-triage-agent-imported-optimized-2af601ca-fd59-41e3-931c-228c1252918e'\nversion='0.1.0'\nscore_pass_threshold=0.8\n",
        encoding="utf-8",
    )
    artifact_dir = tmp_path / "artifacts-legacy"
    artifact_dir.mkdir()
    artifact_path = artifact_dir / "program.json"
    artifact_path.write_text('{"answer": "Paris"}', encoding="utf-8")
    branch_root = tmp_path / "worktree-legacy"

    async def fake_get_optimization_job(job_id):
        return {
            "id": job_id,
            "status": "succeeded",
            "module_import_id": "mod-1",
            "artifact_path": str(artifact_path),
            "request_config": {"target_bundle_version": "0.1.1"},
            "normalized_config": {},
        }

    async def fake_get_module(module_id):
        if module_id == "mod-1":
            return {
                "id": "mod-1",
                "source": "github",
                "bundle_name": "support-triage-agent-imported-optimized-2af601ca-fd59-41e3-931c-228c1252918e",
                "bundle_version": "0.1.0",
                "source_ref": str(source_bundle),
                "checkout_path": str(repo_root),
                "github_branch": "main",
                "github_subpath": "bundles/source-bundle",
            }
        return None

    async def fake_create_worktree(repo_root_arg, *, base_branch, optimization_job_id):
        assert repo_root_arg == repo_root
        shutil.copytree(repo_root, branch_root)
        return branch_root, "optimization-opt"

    async def fake_remove_worktree(repo_root_arg, worktree_path):
        assert repo_root_arg == repo_root
        assert worktree_path == branch_root

    async def fake_create_noncurrent_bundle_revision(module_id, **kwargs):
        assert module_id == "mod-1"
        assert kwargs["bundle_name"] == "support-triage-agent"
        assert kwargs["bundle_version"] == "0.1.1"
        return "rev-after"

    async def fake_run_git_command(args, *, cwd=None):
        if args[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            if str(cwd) == str(branch_root):
                return "optimization-opt"
            return "main"
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return "commit-after"
        return ""

    async def fake_get_updated_module(module_id):
        return {
            "id": "mod-1",
            "source": "github",
            "source_ref": str(source_bundle),
            "checkout_path": str(repo_root),
            "github_branch": "main",
            "github_subpath": "bundles/source-bundle",
            "bundle_name": "support-triage-agent",
            "bundle_version": "0.1.0",
            "validation_status": "passed",
            "smoke_status": "pending",
            "diagnostics": [],
            "current_revision_id": "rev-before",
        }

    async def fake_ensure_module_mutation_allowed(module_id):
        return {"sync_status": "synced"}

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "get_module", fake_get_updated_module)
    monkeypatch.setattr(services, "_create_optimization_worktree", fake_create_worktree)
    monkeypatch.setattr(services, "_remove_optimization_worktree", fake_remove_worktree)
    monkeypatch.setattr(services, "_create_noncurrent_bundle_revision", fake_create_noncurrent_bundle_revision)
    monkeypatch.setattr(services, "_run_git_command", fake_run_git_command)
    monkeypatch.setattr(services, "ensure_module_mutation_allowed", fake_ensure_module_mutation_allowed)

    result = asyncio.run(services.materialize_optimized_bundle("opt-legacy"))

    assert result is not None
    assert result["resulting_bundle_branch"] == "optimization-opt"
    bundle_toml = (branch_root / "bundles" / "source-bundle" / "bundle.toml").read_text(encoding="utf-8")
    assert 'name = "support-triage-agent"' in bundle_toml
    assert 'version = "0.1.1"' in bundle_toml


def test_run_optimization_job_fails_when_writeback_preflight_blocks(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "job": {
            "id": "opt-blocked",
            "status": "queued",
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": str(FIXTURES / "valid_bundle"),
            "strategy": "bootstrap_fewshot",
            "dataset_id": None,
            "validation_dataset_id": None,
            "execution_lm_profile_id": None,
            "helper_lm_profile_id": None,
            "normalized_config": {"dspy_config": {}},
            "train_inputs": [
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}, "prediction": {"answer": "Paris"}}
            ],
            "val_inputs": [],
            "num_threads": 1,
            "source_run_plan_id": "plan-1",
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
        assert job_id == "opt-blocked"
        return dict(state["job"])

    async def fake_get_source_run_plan_baseline(**kwargs):
        return None

    async def fake_append_optimization_process_log(job_id, additions):
        return None

    async def fake_apply_writeback(job_payload, *, bundle_name=None, bundle_version=None):
        raise ModuleSyncError("module has upstream changes that must be synced before mutation", sync_state={"sync_status": "behind"})

    def fake_run_bundle_optimization(**kwargs):
        return {
            "artifact_path": "/tmp/dspy-trainer/optimization_artifacts/opt-blocked/program.json",
            "artifact_metadata": {"artifact_type": "dspy_program_state"},
            "telemetry_summary": {"strategy": "bootstrap_fewshot"},
        }

    monkeypatch.setattr(services, "get_optimization_job", fake_get_optimization_job)
    monkeypatch.setattr(services, "_get_source_run_plan_baseline", fake_get_source_run_plan_baseline)
    monkeypatch.setattr(services, "append_optimization_process_log", fake_append_optimization_process_log)
    monkeypatch.setattr(services, "_apply_optimized_bundle_writeback", fake_apply_writeback)
    monkeypatch.setattr("app.executor.module_runner.run_bundle_optimization", fake_run_bundle_optimization)

    result = asyncio.run(services.run_optimization_job("opt-blocked"))

    assert result is not None
    assert result["status"] == "failed"
    assert "module has upstream changes that must be synced before mutation" in result["failure_reason"]
