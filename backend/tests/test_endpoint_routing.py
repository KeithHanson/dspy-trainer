import asyncio
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices, EndpointUnavailableError


class FakeRedis:
    def __init__(self, payloads=None):
        self.payloads = payloads or {}

    async def keys(self, pattern):
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        return [key for key in self.payloads if key.startswith(prefix)]

    async def get(self, key):
        return self.payloads.get(key)

    async def set(self, key, value, ex=None):
        del ex
        self.payloads[key] = value

    async def delete(self, key):
        self.payloads.pop(key, None)


async def _no_reconcile(self):
    return None


async def _desired_revision(self, endpoint_id):
    del endpoint_id
    return "rev-1"


def _services(worker_payloads, assignment_payloads):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    services.redis = FakeRedis({**worker_payloads, **assignment_payloads})
    services.reconcile_endpoint_worker_assignments = _no_reconcile.__get__(services, AppServices)
    services._get_endpoint_desired_revision_id = _desired_revision.__get__(services, AppServices)
    return services


def test_endpoint_routing_accepts_only_listening_workers_for_assigned_endpoint():
    services = _services(
        {
            "dspy-trainer:endpoint-workers:endpoint-worker-1": json.dumps(
                {
                    "worker_id": "endpoint-worker-1",
                    "status": "listening",
                    "endpoint_id": "endpoint-1",
                    "desired_revision_id": "rev-1",
                    "warmed_revision_id": "rev-1",
                }
            )
        },
        {
            "dspy-trainer:endpoint-worker-assignments:endpoint-worker-1": json.dumps(
                {"worker_id": "endpoint-worker-1", "endpoint_id": "endpoint-1"}
            )
        },
    )

    routing_state = asyncio.run(services.ensure_endpoint_ready_for_invocation("endpoint-1"))

    assert routing_state == {
        "endpoint_id": "endpoint-1",
        "desired_revision_id": "rev-1",
        "assigned_workers": 1,
        "ready_workers": 1,
        "status_counts": {"listening": 1},
    }


@pytest.mark.parametrize(
    ("worker_status", "worker_endpoint_id", "assignment_endpoint_id", "expected_code", "expected_counts"),
    [
        ("preparing", "endpoint-1", "endpoint-1", "no_ready_workers", {"preparing": 1}),
        ("failed", "endpoint-1", "endpoint-1", "no_ready_workers", {"failed": 1}),
        ("listening", "endpoint-1", "endpoint-2", "no_assigned_workers", {}),
    ],
)
def test_endpoint_routing_rejects_preparing_failed_and_unassigned_workers(
    worker_status,
    worker_endpoint_id,
    assignment_endpoint_id,
    expected_code,
    expected_counts,
):
    services = _services(
        {
            "dspy-trainer:endpoint-workers:endpoint-worker-1": json.dumps(
                {
                    "worker_id": "endpoint-worker-1",
                    "status": worker_status,
                    "endpoint_id": worker_endpoint_id,
                    "desired_revision_id": "rev-1" if assignment_endpoint_id == "endpoint-1" else None,
                    "warmed_revision_id": "rev-1" if worker_status == "listening" and assignment_endpoint_id == "endpoint-1" else None,
                }
            )
        },
        {
            "dspy-trainer:endpoint-worker-assignments:endpoint-worker-1": json.dumps(
                {"worker_id": "endpoint-worker-1", "endpoint_id": assignment_endpoint_id}
            )
        },
    )

    with pytest.raises(EndpointUnavailableError) as exc_info:
        asyncio.run(services.ensure_endpoint_ready_for_invocation("endpoint-1"))

    assert exc_info.value.code == expected_code
    assert exc_info.value.routing_state == {
        "endpoint_id": "endpoint-1",
        "desired_revision_id": "rev-1",
        "assigned_workers": 0 if expected_code == "no_assigned_workers" else 1,
        "ready_workers": 0,
        "status_counts": expected_counts,
    }
