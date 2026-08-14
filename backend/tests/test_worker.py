import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"

from app.executor.module_runner import BundleRuntime, invoke_warmed_bundle
from worker import process_job
from endpoint_worker import (
    _build_runtime_identity,
    _heartbeat,
    _log_assignment_transition,
    ensure_endpoint_assignment_ready,
    process_endpoint_job,
    resolve_endpoint_worker_id,
)


class FakeRedis:
    def __init__(self):
        self.calls = []
        self.values = {}

    async def set(self, key, value, ex=None, nx=False, xx=False):
        self.calls.append((key, value, ex, nx, xx))
        if nx and key in self.values:
            return False
        if xx and key not in self.values:
            return False
        self.values[key] = value
        return True


class FakeServices:
    def __init__(self):
        self.redis = FakeRedis()
        self.postgres_pool = None
        self.settings = SimpleNamespace(worker_registry_prefix="dspy-trainer:workers")
        self.optimization_job_ids = []
        self.agent_run_task_ids = []
        self.process_log_updates = []
        self.fail_agent_run = False
        self.endpoint_invocations = []
        self.fail_endpoint_invocation = False
        self.bundle_requirement_installs = []
        self.bundle_revision_id = "rev-1"
        self.restart_generation = 0
        self.bundle_path = str(FIXTURES / "valid_bundle")
        self.runtime_env = {"SPECIAL_TOKEN": "expected-value"}
        self.lm_profile = None
        self.registry_workers = {}
        self.registry_calls = []

    async def append_optimization_process_log(self, optimization_job_id, additions):
        self.process_log_updates.append((optimization_job_id, additions))

    async def run_optimization_job(self, optimization_job_id):
        self.optimization_job_ids.append(optimization_job_id)
        return {"id": optimization_job_id, "status": "succeeded"}

    async def run_agent_run_task(self, task_id, worker_id):
        self.agent_run_task_ids.append((task_id, worker_id))
        if self.fail_agent_run:
            raise RuntimeError("boom")
        return {"id": task_id, "status": "succeeded"}

    async def run_endpoint_invocation_job(self, invocation_id, endpoint_id, input_payload, worker_id, *, stream, warmed_runtime=None, runtime_env_override=None):
        self.endpoint_invocations.append((invocation_id, endpoint_id, input_payload, worker_id, stream, warmed_runtime, runtime_env_override))
        if self.fail_endpoint_invocation:
            raise RuntimeError("endpoint invocation boom")

    async def get_bundle_endpoint(self, endpoint_id):
        return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": self.bundle_revision_id, "restart_generation": self.restart_generation, "lm_profile_id": None}

    async def resolve_module_execution_state(self, module_id):
        return {"module_id": module_id, "bundle_path": self.bundle_path, "bundle_revision_id": self.bundle_revision_id}

    async def resolve_bundle_endpoint_execution_state(self, endpoint_id):
        del endpoint_id
        return {"module_id": "mod-1", "bundle_path": self.bundle_path, "bundle_revision_id": self.bundle_revision_id, "restart_generation": self.restart_generation}

    async def ensure_bundle_requirements_installed(self, bundle_path):
        self.bundle_requirement_installs.append(bundle_path)

    async def get_module_runtime_environment(self, module_id):
        assert module_id == "mod-1"
        return dict(self.runtime_env)

    async def _get_lm_profile_record(self, profile_id, *, include_secret=False):
        assert profile_id is None or profile_id == "profile-1"
        assert include_secret is True
        return self.lm_profile

    async def register_endpoint_worker(self, **payload):
        worker_id = payload.get("worker_id") or f"endpoint-worker-{len(self.registry_workers) + 1}"
        record = {**payload, "worker_id": worker_id}
        self.registry_workers[worker_id] = record
        self.registry_calls.append(("register", record))
        return record

    async def heartbeat_endpoint_worker(self, worker_id, **payload):
        record = self.registry_workers.get(worker_id)
        if record is None:
            return None
        record = {**record, **payload, "worker_id": worker_id}
        self.registry_workers[worker_id] = record
        self.registry_calls.append(("heartbeat", record))
        return record


def test_process_job_runs_optimization_job_payload():
    services = FakeServices()

    asyncio.run(
        process_job(
            cast(Any, services),
            json.dumps({"type": "optimization_job", "job_id": "opt-123"}),
            worker_id="worker-1",
        )
    )

    assert services.optimization_job_ids == ["opt-123"]
    assert services.process_log_updates[0][0] == "opt-123"
    assert "status=worker_picked_up" in services.process_log_updates[0][1]
    assert json.loads(services.redis.calls[0][1])["status"] == "running"
    assert json.loads(services.redis.calls[-1][1])["status"] == "listening"


def test_process_job_runs_agent_run_task_payload_and_restores_listening():
    services = FakeServices()

    asyncio.run(
        process_job(
            cast(Any, services),
            json.dumps({"type": "agent_run_task", "task_id": "task-123"}),
            worker_id="worker-1",
        )
    )

    assert services.agent_run_task_ids == [("task-123", "worker-1")]
    assert json.loads(services.redis.calls[0][1])["status"] == "running"
    assert json.loads(services.redis.calls[-1][1])["status"] == "listening"


def test_process_job_restores_listening_when_agent_run_task_fails():
    services = FakeServices()
    services.fail_agent_run = True

    try:
        asyncio.run(
            process_job(
                cast(Any, services),
                json.dumps({"type": "agent_run_task", "task_id": "task-123"}),
                worker_id="worker-1",
            )
        )
    except RuntimeError:
        pass

    assert json.loads(services.redis.calls[0][1])["status"] == "running"
    assert json.loads(services.redis.calls[-1][1])["status"] == "listening"


def test_resolve_endpoint_worker_id_prefers_explicit_id():
    assert resolve_endpoint_worker_id("endpoint-worker-9", hostname="stack-endpoint-worker-2", pid=1234) == "endpoint-worker-9"


def test_resolve_endpoint_worker_id_falls_back_to_hostname_and_pid():
    assert resolve_endpoint_worker_id(None, hostname="devbox-7", pid=1234) == "devbox-7-1234"


def test_endpoint_worker_self_registers_through_registry_with_runtime_metadata():
    services = FakeServices()
    services.postgres_pool = object()
    runtime_identity = _build_runtime_identity()

    worker_id = asyncio.run(
        _heartbeat(
            cast(Any, services),
            "",
            "idle",
            runtime_identity=runtime_identity,
            registration=True,
        )
    )

    assert worker_id == "endpoint-worker-1"
    registered = services.registry_workers[worker_id]
    assert registered["runtime_instance_id"] == runtime_identity["runtime_instance_id"]
    assert registered["runtime_metadata"]["booted_at"] == runtime_identity["booted_at"]
    assert registered["runtime_metadata"]["hostname"] == runtime_identity["hostname"]
    assert registered["runtime_metadata"]["pid"] == runtime_identity["pid"]
    assert services.redis.calls == []


def test_endpoint_worker_heartbeats_update_registry_status_and_assignment_metadata():
    services = FakeServices()
    services.postgres_pool = object()
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-9")

    asyncio.run(
        _heartbeat(
            cast(Any, services),
            "endpoint-worker-9",
            "idle",
            runtime_identity=runtime_identity,
            registration=True,
        )
    )
    asyncio.run(
        _heartbeat(
            cast(Any, services),
            "endpoint-worker-9",
            "running",
            task_id="inv-1",
            endpoint_id="endpoint-1",
            desired_revision_id="rev-2",
            warmed_revision_id="rev-1",
            runtime_identity=runtime_identity,
        )
    )

    assert [call[0] for call in services.registry_calls] == ["register", "heartbeat"]
    heartbeat = services.registry_workers["endpoint-worker-9"]
    assert heartbeat["status"] == "running"
    assert heartbeat["assigned_endpoint_id"] == "endpoint-1"
    assert heartbeat["task_id"] == "inv-1"
    assert heartbeat["runtime_metadata"]["desired_revision_id"] == "rev-2"
    assert heartbeat["runtime_metadata"]["warmed_revision_id"] == "rev-1"
    assert heartbeat["runtime_metadata"]["desired_restart_generation"] is None
    assert heartbeat["runtime_metadata"]["warmed_restart_generation"] is None


def test_endpoint_worker_heartbeat_preserves_revision_metadata_across_minimal_heartbeats():
    services = FakeServices()
    services.postgres_pool = object()
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    asyncio.run(
        _heartbeat(
            cast(Any, services),
            "endpoint-worker-1",
            "listening",
            endpoint_id="endpoint-1",
            desired_revision_id="rev-1",
            warmed_revision_id="rev-1",
            runtime_identity=runtime_identity,
        )
    )
    asyncio.run(
        _heartbeat(
            cast(Any, services),
            "endpoint-worker-1",
            "listening",
            endpoint_id="endpoint-1",
            runtime_identity=runtime_identity,
        )
    )

    heartbeat = services.registry_calls[-1][1]
    assert heartbeat["runtime_metadata"]["desired_revision_id"] == "rev-1"
    assert heartbeat["runtime_metadata"]["warmed_revision_id"] == "rev-1"
    assert heartbeat["runtime_metadata"]["desired_restart_generation"] is None
    assert heartbeat["runtime_metadata"]["warmed_restart_generation"] is None
    assert heartbeat["runtime_metadata"]["endpoint_id"] == "endpoint-1"


def test_process_endpoint_job_runs_endpoint_invocation_and_restores_listening(caplog):
    services = FakeServices()
    services.postgres_pool = object()
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    with caplog.at_level("INFO"):
        asyncio.run(
            process_endpoint_job(
                cast(Any, services),
                json.dumps({"type": "endpoint_invocation", "invocation_id": "inv-1", "input_payload": {"question": "hello"}, "stream": True}),
                worker_id="endpoint-worker-1",
                endpoint_id="endpoint-1",
                revision_id="rev-1",
                runtime_identity=runtime_identity,
                restart_generation=2,
            )
        )

    assert services.endpoint_invocations == [("inv-1", "endpoint-1", {"question": "hello"}, "endpoint-worker-1", True, None, None)]
    assert services.registry_calls[1][1]["status"] == "running"
    assert services.registry_calls[1][1]["runtime_metadata"]["warmed_revision_id"] == "rev-1"
    assert services.registry_calls[1][1]["runtime_metadata"]["warmed_restart_generation"] == 2
    assert services.registry_calls[-1][1]["status"] == "listening"
    assert services.registry_calls[-1][1]["runtime_metadata"]["desired_revision_id"] == "rev-1"
    assert services.registry_calls[-1][1]["runtime_metadata"]["desired_restart_generation"] == 2
    assert "event=endpoint_worker.invocation_started" in caplog.text
    assert "event=endpoint_worker.invocation_finished" in caplog.text
    assert "invocation_id=inv-1" in caplog.text


def test_ensure_endpoint_assignment_ready_preinstalls_dependencies_and_marks_listening(caplog):
    services = FakeServices()
    services.postgres_pool = object()
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    with caplog.at_level("INFO"):
        ready_revision_id, ready_restart_generation, warmed_runtime, warmed_runtime_env = asyncio.run(
            ensure_endpoint_assignment_ready(cast(Any, services), "endpoint-worker-1", "endpoint-1", runtime_identity=runtime_identity)
        )

    assert ready_revision_id == "rev-1"
    assert ready_restart_generation == 0
    assert isinstance(warmed_runtime, BundleRuntime)
    assert warmed_runtime_env == {"SPECIAL_TOKEN": "expected-value"}
    assert services.bundle_requirement_installs == [services.bundle_path]
    statuses = [call[1]["status"] for call in services.registry_calls[1:]]
    assert statuses == ["preparing", "listening"]
    assert services.registry_calls[-1][1]["runtime_metadata"]["warmed_revision_id"] == "rev-1"
    assert services.registry_calls[-1][1]["runtime_metadata"]["warmed_restart_generation"] == 0
    assert "event=endpoint_worker.runtime_decision" in caplog.text
    assert "action=warmup_required" in caplog.text
    assert "event=endpoint_worker.warmup_started" in caplog.text
    assert "event=endpoint_worker.warmup_succeeded" in caplog.text
    assert 'reasons=["runtime_missing", "revision_changed", "restart_generation_changed"]' in caplog.text


def test_ensure_endpoint_assignment_ready_rewarms_when_revision_changes():
    services = FakeServices()
    services.postgres_pool = object()
    services.bundle_revision_id = "rev-2"
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    ready_revision_id, ready_restart_generation, warmed_runtime, warmed_runtime_env = asyncio.run(
        ensure_endpoint_assignment_ready(
            cast(Any, services),
            "endpoint-worker-1",
            "endpoint-1",
            warmed_revision_id="rev-1",
            runtime_identity=runtime_identity,
        )
    )

    statuses = [call[1]["status"] for call in services.registry_calls[1:]]
    assert ready_revision_id == "rev-2"
    assert ready_restart_generation == 0
    assert isinstance(warmed_runtime, BundleRuntime)
    assert warmed_runtime_env == {"SPECIAL_TOKEN": "expected-value"}
    assert statuses == ["preparing", "listening"]
    assert services.bundle_requirement_installs == [services.bundle_path]
    assert services.registry_calls[1][1]["runtime_metadata"]["warmed_revision_id"] == "rev-1"
    assert services.registry_calls[-1][1]["runtime_metadata"]["warmed_revision_id"] == "rev-2"


def test_ensure_endpoint_assignment_ready_reloads_changed_forward_signature_without_restart(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Program(dspy.Module):\n"
        "  def forward(self, question: str):\n"
        "    return dspy.Prediction(answer=question.upper())\n"
        "def build_program():\n"
        "  return Program()\n",
        encoding="utf-8",
    )

    services = FakeServices()
    services.postgres_pool = object()
    services.bundle_path = str(bundle)
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    ready_revision_id_v1, ready_restart_generation_v1, warmed_runtime_v1, _ = asyncio.run(
        ensure_endpoint_assignment_ready(cast(Any, services), "endpoint-worker-1", "endpoint-1", runtime_identity=runtime_identity)
    )

    assert ready_revision_id_v1 == "rev-1"
    assert ready_restart_generation_v1 == 0
    assert isinstance(warmed_runtime_v1, BundleRuntime)
    runtime_v1_module_name = warmed_runtime_v1.program.__class__.__module__
    assert invoke_warmed_bundle(warmed_runtime_v1, {"question": "hello"}) == {"answer": "HELLO"}

    services.bundle_revision_id = "rev-2"
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Program(dspy.Module):\n"
        "  def forward(self, prompt: str):\n"
        "    return dspy.Prediction(answer=prompt.lower())\n"
        "def build_program():\n"
        "  return Program()\n",
        encoding="utf-8",
    )

    ready_revision_id_v2, ready_restart_generation_v2, warmed_runtime_v2, _ = asyncio.run(
        ensure_endpoint_assignment_ready(
            cast(Any, services),
            "endpoint-worker-1",
            "endpoint-1",
            warmed_revision_id="rev-1",
            runtime_identity=runtime_identity,
            warmed_runtime=warmed_runtime_v1,
        )
    )

    assert ready_revision_id_v2 == "rev-2"
    assert ready_restart_generation_v2 == 0
    assert isinstance(warmed_runtime_v2, BundleRuntime)
    assert warmed_runtime_v2.program.__class__.__module__ != runtime_v1_module_name
    assert invoke_warmed_bundle(warmed_runtime_v2, {"prompt": "HELLO"}) == {"answer": "hello"}
    with pytest.raises(TypeError, match="unexpected keyword argument 'question'"):
        invoke_warmed_bundle(warmed_runtime_v2, {"question": "hello"})


def test_ensure_endpoint_assignment_ready_skips_warmup_when_revision_matches():
    services = FakeServices()
    services.postgres_pool = object()
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    warmed_runtime = BundleRuntime(bundle_path=services.bundle_path, program=object(), lm=None)
    ready_revision_id, ready_restart_generation, next_runtime, warmed_runtime_env = asyncio.run(
        ensure_endpoint_assignment_ready(
            cast(Any, services),
            "endpoint-worker-1",
            "endpoint-1",
            warmed_revision_id="rev-1",
            warmed_restart_generation=0,
            runtime_identity=runtime_identity,
            warmed_runtime=warmed_runtime,
        )
    )

    assert ready_revision_id == "rev-1"
    assert ready_restart_generation == 0
    assert next_runtime is warmed_runtime
    assert warmed_runtime_env == {"SPECIAL_TOKEN": "expected-value"}
    assert services.bundle_requirement_installs == []
    assert [call[1]["status"] for call in services.registry_calls[1:]] == ["listening"]


def test_ensure_endpoint_assignment_ready_rewarms_when_restart_generation_changes():
    services = FakeServices()
    services.postgres_pool = object()
    services.restart_generation = 3
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    warmed_runtime = BundleRuntime(bundle_path=services.bundle_path, program=object(), lm=None)
    ready_revision_id, ready_restart_generation, next_runtime, warmed_runtime_env = asyncio.run(
        ensure_endpoint_assignment_ready(
            cast(Any, services),
            "endpoint-worker-1",
            "endpoint-1",
            warmed_revision_id="rev-1",
            warmed_restart_generation=2,
            runtime_identity=runtime_identity,
            warmed_runtime=warmed_runtime,
        )
    )

    assert ready_revision_id == "rev-1"
    assert ready_restart_generation == 3
    assert isinstance(next_runtime, BundleRuntime)
    assert next_runtime is not warmed_runtime
    assert warmed_runtime_env == {"SPECIAL_TOKEN": "expected-value"}
    assert services.bundle_requirement_installs == [services.bundle_path]
    assert [call[1]["status"] for call in services.registry_calls[1:]] == ["preparing", "listening"]
    assert services.registry_calls[1][1]["runtime_metadata"]["warmed_restart_generation"] == 2
    assert services.registry_calls[-1][1]["runtime_metadata"]["warmed_restart_generation"] == 3


def test_ensure_endpoint_assignment_ready_marks_worker_failed_when_revision_metadata_missing(caplog):
    services = FakeServices()
    services.postgres_pool = object()
    services.bundle_revision_id = None
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    with caplog.at_level("INFO"):
        ready_revision_id, ready_restart_generation, warmed_runtime, warmed_runtime_env = asyncio.run(
            ensure_endpoint_assignment_ready(
                cast(Any, services),
                "endpoint-worker-1",
                "endpoint-1",
                warmed_revision_id="rev-1",
                runtime_identity=runtime_identity,
            )
        )

    assert ready_revision_id is None
    assert ready_restart_generation is None
    assert warmed_runtime is None
    assert warmed_runtime_env is None
    assert services.bundle_requirement_installs == []
    assert [call[1]["status"] for call in services.registry_calls[1:]] == ["failed"]
    assert services.registry_calls[-1][1]["assigned_endpoint_id"] == "endpoint-1"
    assert services.registry_calls[-1][1]["last_error"] == "revision_metadata_missing"
    assert services.registry_calls[-1][1]["runtime_metadata"]["desired_revision_id"] is None
    assert services.registry_calls[-1][1]["runtime_metadata"]["warmed_revision_id"] == "rev-1"
    assert "event=endpoint_worker.assignment_not_ready" in caplog.text
    assert "reason=revision_metadata_missing" in caplog.text


def test_process_endpoint_job_logs_failure_and_restores_listening(caplog):
    services = FakeServices()
    services.postgres_pool = object()
    services.fail_endpoint_invocation = True
    runtime_identity = _build_runtime_identity(explicit_worker_id="endpoint-worker-1")
    asyncio.run(
        _heartbeat(cast(Any, services), "endpoint-worker-1", "idle", runtime_identity=runtime_identity, registration=True)
    )

    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError, match="endpoint invocation boom"):
            asyncio.run(
                process_endpoint_job(
                    cast(Any, services),
                    json.dumps({"type": "endpoint_invocation", "invocation_id": "inv-2", "input_payload": {"question": "hello"}, "stream": False}),
                    worker_id="endpoint-worker-1",
                    endpoint_id="endpoint-1",
                    revision_id="rev-1",
                    runtime_identity=runtime_identity,
                    restart_generation=2,
                )
            )

    assert services.registry_calls[-1][1]["status"] == "listening"
    assert "event=endpoint_worker.invocation_failed" in caplog.text
    assert 'error="endpoint invocation boom"' in caplog.text


def test_log_assignment_transition_emits_change_and_loss(caplog):
    with caplog.at_level("INFO"):
        _log_assignment_transition("endpoint-worker-1", None, "endpoint-1")
        _log_assignment_transition("endpoint-worker-1", "endpoint-1", None)

    assert "event=endpoint_worker.assignment_changed" in caplog.text
    assert "event=endpoint_worker.assignment_lost" in caplog.text
