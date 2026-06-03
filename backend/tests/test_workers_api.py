import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class FakeRedis:
    def __init__(self, payloads):
        self.payloads = payloads

    async def keys(self, pattern):
        del pattern
        return list(self.payloads.keys())

    async def get(self, key):
        return self.payloads.get(key)


def test_list_workers_reports_configured_total_even_when_some_workers_are_missing():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", total_workers=8))
    setattr(
        services,
        "redis",
        FakeRedis(
        {
            "dspy-trainer:workers:worker-1": '{"worker_id":"worker-1","status":"listening","task_id":null,"last_seen":"2026-01-01T00:00:00+00:00"}',
            "dspy-trainer:workers:worker-2": '{"worker_id":"worker-2","status":"running","task_id":"task-2","last_seen":"2026-01-01T00:00:00+00:00"}',
        }
        ),
    )

    payload = asyncio.run(services.list_workers())

    assert payload["total_workers"] == 8
    assert payload["reported_workers"] == 2
    assert payload["available_workers"] == 1
    assert payload["busy_workers"] == 1
    assert [item["worker_id"] for item in payload["items"]] == ["worker-1", "worker-2"]
