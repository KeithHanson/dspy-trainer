import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import process_job
from endpoint_worker import ensure_endpoint_assignment_ready, process_endpoint_job


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def set(self, key, value, ex=None):
        self.calls.append((key, value, ex))


class FakeServices:
    def __init__(self):
        self.redis = FakeRedis()
        self.settings = SimpleNamespace(worker_registry_prefix="dspy-trainer:workers")
        self.optimization_job_ids = []
        self.agent_run_task_ids = []
        self.process_log_updates = []
        self.fail_agent_run = False
        self.endpoint_invocations = []
        self.bundle_requirement_installs = []
        self.bundle_revision_id = "rev-1"

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

    async def run_endpoint_invocation_job(self, invocation_id, endpoint_id, input_payload, worker_id, *, stream):
        self.endpoint_invocations.append((invocation_id, endpoint_id, input_payload, worker_id, stream))

    async def get_bundle_endpoint(self, endpoint_id):
        return {"id": endpoint_id, "module_import_id": "mod-1"}

    async def resolve_module_execution_state(self, module_id):
        return {"module_id": module_id, "bundle_path": "/tmp/bundle", "bundle_revision_id": self.bundle_revision_id}

    async def ensure_bundle_requirements_installed(self, bundle_path):
        self.bundle_requirement_installs.append(bundle_path)


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


def test_process_endpoint_job_runs_endpoint_invocation_and_restores_listening():
    services = FakeServices()
    services.settings = SimpleNamespace(
        worker_registry_prefix="dspy-trainer:workers",
        endpoint_worker_registry_prefix="dspy-trainer:endpoint-workers",
        endpoint_worker_inventory_prefix="dspy-trainer:endpoint-worker-inventory",
    )

    asyncio.run(
        process_endpoint_job(
            cast(Any, services),
            json.dumps({"type": "endpoint_invocation", "invocation_id": "inv-1", "input_payload": {"question": "hello"}, "stream": True}),
            worker_id="endpoint-worker-1",
            endpoint_id="endpoint-1",
            revision_id="rev-1",
        )
    )

    assert services.endpoint_invocations == [("inv-1", "endpoint-1", {"question": "hello"}, "endpoint-worker-1", True)]
    live_calls = [json.loads(value) for key, value, _ in services.redis.calls if key.startswith("dspy-trainer:endpoint-workers:")]
    inventory_calls = [json.loads(value) for key, value, _ in services.redis.calls if key.startswith("dspy-trainer:endpoint-worker-inventory:")]
    assert live_calls[0]["status"] == "running"
    assert live_calls[0]["warmed_revision_id"] == "rev-1"
    assert live_calls[-1]["status"] == "listening"
    assert inventory_calls[-1]["desired_revision_id"] == "rev-1"
    assert inventory_calls[-1]["last_heartbeat_status"] == "listening"


def test_ensure_endpoint_assignment_ready_preinstalls_dependencies_and_marks_listening():
    services = FakeServices()
    services.settings = SimpleNamespace(
        endpoint_worker_registry_prefix="dspy-trainer:endpoint-workers",
        endpoint_worker_inventory_prefix="dspy-trainer:endpoint-worker-inventory",
    )

    ready_revision_id = asyncio.run(ensure_endpoint_assignment_ready(cast(Any, services), "endpoint-worker-1", "endpoint-1"))

    assert ready_revision_id == "rev-1"
    assert services.bundle_requirement_installs == ["/tmp/bundle"]
    live_calls = [json.loads(value) for key, value, _ in services.redis.calls if key.startswith("dspy-trainer:endpoint-workers:")]
    assert live_calls[0]["status"] == "stale"
    assert live_calls[1]["status"] == "preparing"
    assert live_calls[-1]["status"] == "listening"
    assert live_calls[-1]["warmed_revision_id"] == "rev-1"


def test_ensure_endpoint_assignment_ready_rewarms_when_revision_changes():
    services = FakeServices()
    services.settings = SimpleNamespace(
        endpoint_worker_registry_prefix="dspy-trainer:endpoint-workers",
        endpoint_worker_inventory_prefix="dspy-trainer:endpoint-worker-inventory",
    )
    services.bundle_revision_id = "rev-2"

    ready_revision_id = asyncio.run(
        ensure_endpoint_assignment_ready(cast(Any, services), "endpoint-worker-1", "endpoint-1", warmed_revision_id="rev-1")
    )

    live_calls = [json.loads(value) for key, value, _ in services.redis.calls if key.startswith("dspy-trainer:endpoint-workers:")]
    statuses = [payload["status"] for payload in live_calls]
    assert ready_revision_id == "rev-2"
    assert statuses == ["stale", "preparing", "listening"]
    assert services.bundle_requirement_installs == ["/tmp/bundle"]
    assert live_calls[0]["warmed_revision_id"] == "rev-1"
    assert live_calls[-1]["warmed_revision_id"] == "rev-2"


def test_ensure_endpoint_assignment_ready_skips_warmup_when_revision_matches():
    services = FakeServices()
    services.settings = SimpleNamespace(
        endpoint_worker_registry_prefix="dspy-trainer:endpoint-workers",
        endpoint_worker_inventory_prefix="dspy-trainer:endpoint-worker-inventory",
    )

    ready_revision_id = asyncio.run(
        ensure_endpoint_assignment_ready(cast(Any, services), "endpoint-worker-1", "endpoint-1", warmed_revision_id="rev-1")
    )

    assert ready_revision_id == "rev-1"
    assert services.bundle_requirement_installs == []
    live_calls = [json.loads(value) for key, value, _ in services.redis.calls if key.startswith("dspy-trainer:endpoint-workers:")]
    assert [payload["status"] for payload in live_calls] == ["listening"]
