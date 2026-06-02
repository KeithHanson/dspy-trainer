import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import process_job


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
        self.process_log_updates = []

    async def append_optimization_process_log(self, optimization_job_id, additions):
        self.process_log_updates.append((optimization_job_id, additions))

    async def run_optimization_job(self, optimization_job_id):
        self.optimization_job_ids.append(optimization_job_id)
        return {"id": optimization_job_id, "status": "succeeded"}


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
    assert len(services.redis.calls) == 2
