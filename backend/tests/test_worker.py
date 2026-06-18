import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import process_job
from endpoint_worker import process_endpoint_job


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
    )

    asyncio.run(
        process_endpoint_job(
            cast(Any, services),
            json.dumps({"type": "endpoint_invocation", "invocation_id": "inv-1", "input_payload": {"question": "hello"}, "stream": True}),
            worker_id="endpoint-worker-1",
            endpoint_id="endpoint-1",
        )
    )

    assert services.endpoint_invocations == [("inv-1", "endpoint-1", {"question": "hello"}, "endpoint-worker-1", True)]
    assert json.loads(services.redis.calls[0][1])["status"] == "running"
    assert json.loads(services.redis.calls[-1][1])["status"] == "listening"
