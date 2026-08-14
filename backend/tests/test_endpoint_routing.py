import asyncio
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices, EndpointUnavailableError


async def _no_reconcile(self):
    return None


def _services(items, *, deployed_revision_id="rev-1", restart_generation=0):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    services.postgres_pool = object()

    async def list_endpoint_workers():
        return {"items": items}

    async def get_bundle_endpoint(endpoint_id):
        return {
            "id": endpoint_id,
            "module_import_id": "mod-1",
            "deployed_revision_id": deployed_revision_id,
            "restart_generation": restart_generation,
        }

    services.list_endpoint_workers = list_endpoint_workers  # type: ignore[method-assign]
    services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
    services.reconcile_endpoint_worker_assignments = _no_reconcile.__get__(services, AppServices)
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
                "desired_restart_generation": 0,
                "warmed_restart_generation": 0,
                "is_live": True,
            }
        ]
    )

    routing_state = asyncio.run(services.ensure_endpoint_ready_for_invocation("endpoint-1"))

    assert routing_state == {
        "endpoint_id": "endpoint-1",
        "desired_revision_id": "rev-1",
        "desired_restart_generation": 0,
        "assigned_workers": 1,
        "ready_workers": 1,
        "status_counts": {"listening": 1},
    }


@pytest.mark.parametrize(
    (
        "worker_status",
        "worker_endpoint_id",
        "assigned_endpoint_id",
        "desired_restart_generation",
        "warmed_restart_generation",
        "is_live",
        "expected_code",
        "expected_counts",
    ),
    [
        ("preparing", "endpoint-1", "endpoint-1", 0, None, True, "no_ready_workers", {"preparing": 1}),
        ("failed", "endpoint-1", "endpoint-1", 0, None, True, "no_ready_workers", {"failed": 1}),
        ("listening", "endpoint-1", "endpoint-1", 1, 0, True, "no_ready_workers", {"listening": 1}),
        ("listening", "endpoint-1", "endpoint-2", None, None, True, "no_assigned_workers", {}),
        ("listening", "endpoint-1", "endpoint-1", 0, 0, False, "no_assigned_workers", {}),
    ],
)
def test_endpoint_routing_requires_live_registry_assignments(
    worker_status,
    worker_endpoint_id,
    assigned_endpoint_id,
    desired_restart_generation,
    warmed_restart_generation,
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
                "desired_restart_generation": desired_restart_generation,
                "warmed_restart_generation": warmed_restart_generation,
                "is_live": is_live,
            }
        ],
        restart_generation=1 if desired_restart_generation == 1 else 0,
    )

    with pytest.raises(EndpointUnavailableError) as exc_info:
        asyncio.run(services.ensure_endpoint_ready_for_invocation("endpoint-1"))

    assert exc_info.value.code == expected_code
    assert exc_info.value.routing_state == {
        "endpoint_id": "endpoint-1",
        "desired_revision_id": "rev-1",
        "desired_restart_generation": 1 if desired_restart_generation == 1 else 0,
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
                "desired_restart_generation": 0,
                "warmed_restart_generation": 0,
                "is_live": True,
            },
            {
                "worker_id": "endpoint-worker-2",
                "status": "idle",
                "endpoint_id": None,
                "assigned_endpoint_id": None,
                "desired_revision_id": None,
                "warmed_revision_id": None,
                "desired_restart_generation": None,
                "warmed_restart_generation": None,
                "is_live": True,
            },
        ]
    )

    routing_state = asyncio.run(services.get_endpoint_routing_state("endpoint-1"))

    assert routing_state == {
        "endpoint_id": "endpoint-1",
        "desired_revision_id": "rev-1",
        "desired_restart_generation": 0,
        "assigned_workers": 1,
        "ready_workers": 1,
        "status_counts": {"listening": 1},
    }
