import asyncio
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices, EndpointUnavailableError


async def _no_reconcile(self):
    return None


async def _desired_revision(self, endpoint_id):
    del endpoint_id
    return "rev-1"


def _services(items):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    services.postgres_pool = object()

    async def list_endpoint_workers():
        return {"items": items}

    services.list_endpoint_workers = list_endpoint_workers  # type: ignore[method-assign]
    services.reconcile_endpoint_worker_assignments = _no_reconcile.__get__(services, AppServices)
    services._get_endpoint_desired_revision_id = _desired_revision.__get__(services, AppServices)
    return services


def test_endpoint_routing_accepts_only_listening_workers_for_assigned_endpoint():
    services = _services(
        [
            {
                "worker_id": "endpoint-worker-1",
                "status": "listening",
                "endpoint_id": "endpoint-1",
                "assigned_endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-1",
                "warmed_revision_id": "rev-1",
                "is_live": True,
            }
        ]
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
    ("worker_status", "worker_endpoint_id", "assigned_endpoint_id", "is_live", "expected_code", "expected_counts"),
    [
        ("preparing", "endpoint-1", "endpoint-1", True, "no_ready_workers", {"preparing": 1}),
        ("failed", "endpoint-1", "endpoint-1", True, "no_ready_workers", {"failed": 1}),
        ("listening", "endpoint-1", "endpoint-2", True, "no_assigned_workers", {}),
        ("listening", "endpoint-1", "endpoint-1", False, "no_assigned_workers", {}),
    ],
)
def test_endpoint_routing_requires_live_registry_assignments(
    worker_status,
    worker_endpoint_id,
    assigned_endpoint_id,
    is_live,
    expected_code,
    expected_counts,
):
    services = _services(
        [
            {
                "worker_id": "endpoint-worker-1",
                "status": worker_status,
                "endpoint_id": worker_endpoint_id,
                "assigned_endpoint_id": assigned_endpoint_id,
                "desired_revision_id": "rev-1" if assigned_endpoint_id == "endpoint-1" else None,
                "warmed_revision_id": "rev-1" if worker_status == "listening" and assigned_endpoint_id == "endpoint-1" else None,
                "is_live": is_live,
            }
        ]
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


def test_endpoint_routing_leaves_extra_live_workers_visible_but_unassigned():
    services = _services(
        [
            {
                "worker_id": "endpoint-worker-1",
                "status": "listening",
                "endpoint_id": "endpoint-1",
                "assigned_endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-1",
                "warmed_revision_id": "rev-1",
                "is_live": True,
            },
            {
                "worker_id": "endpoint-worker-2",
                "status": "idle",
                "endpoint_id": None,
                "assigned_endpoint_id": None,
                "desired_revision_id": None,
                "warmed_revision_id": None,
                "is_live": True,
            },
        ]
    )

    routing_state = asyncio.run(services.get_endpoint_routing_state("endpoint-1"))

    assert routing_state == {
        "endpoint_id": "endpoint-1",
        "desired_revision_id": "rev-1",
        "assigned_workers": 1,
        "ready_workers": 1,
        "status_counts": {"listening": 1},
    }
