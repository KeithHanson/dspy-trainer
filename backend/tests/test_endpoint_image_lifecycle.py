import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod
from app.endpoint_container_reconciler import PostgresEndpointContainerStore
from app.services import AppServices

NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _ConnectionContext(self.connection)


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _ImageGateConnection:
    def __init__(self, status, *, validation_ready=True, image_id="sha256:ready"):
        self.status = status
        self.validation_ready = validation_ready
        self.image_id = image_id
        self.writes = []
        self.endpoint = None

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("select id, generation, image_id"):
            if self.status == "ready" and self.image_id is not None:
                return {"id": params[0], "generation": 3, "image_id": self.image_id}
            return None
        if normalized.startswith("select m.id as module_id"):
            build_id = None if self.status == "missing" else "build-1"
            latest_status = None if self.status == "missing" else self.status
            ready = self.status == "ready" and self.image_id is not None
            return {
                "module_id": "module-1",
                "current_revision_id": "revision-1",
                "validation_status": "passed" if self.validation_ready else "failed",
                "validation_revision_id": (
                    "revision-1" if self.validation_ready else "revision-old"
                ),
                "ready_build_id": "build-1" if ready else None,
                "ready_generation": 3 if ready else None,
                "ready_image_id": self.image_id if ready else None,
                "latest_build_id": build_id,
                "latest_generation": 3 if build_id else None,
                "latest_build_status": latest_status,
                "latest_image_id": self.image_id,
                "latest_failure_reason": (
                    "injected build failure" if self.status == "failed" else None
                ),
            }
        if normalized.startswith("insert into bundle_endpoints"):
            self.writes.append(("endpoint", params[0]))
            self.endpoint = {
                "id": params[0],
                "module_import_id": params[1],
                "lm_profile_id": params[2],
                "pinned_worker_count": params[3],
                "name": params[4],
                "key_preview": params[6],
                "created_at": params[7],
                "updated_at": params[8],
                "module_bundle_name": "bundle",
                "lm_profile_name": None,
            }
            return dict(self.endpoint)
        if "from bundle_endpoints e" in normalized:
            return None if self.endpoint is None else dict(self.endpoint)
        raise AssertionError(f"unexpected fetchrow SQL: {normalized}")

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("insert into endpoint_deployments"):
            self.writes.append(
                (
                    "deployment",
                    params[0],
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                )
            )
            return "INSERT 1"
        raise AssertionError(f"unexpected execute SQL: {normalized}")


async def _no_op(*args, **kwargs):
    return None


def _api_services(connection):
    services = AppServices(SimpleNamespace())
    services.postgres_pool = _Pool(connection)
    services.connect_backend = _no_op
    services.disconnect = _no_op

    async def deployment_status(self, endpoint_id):
        return {
            "endpoint_id": endpoint_id,
            "phase": "pending",
            "legacy_fallback": False,
            "migration_state": "rolling",
        }

    services.get_endpoint_deployment_status = MethodType(deployment_status, services)
    return services


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("missing", "missing"),
        ("queued", "queued"),
        ("building", "building"),
        ("failed", "failed"),
        ("pruned", "pruned"),
    ],
)
def test_endpoint_create_rejects_every_nonready_image_without_writes(
    monkeypatch, status, expected_status
):
    connection = _ImageGateConnection(status)
    services = _api_services(connection)
    monkeypatch.setattr(main_mod, "AppServices", lambda settings: services)
    monkeypatch.setattr(main_mod, "get_settings", lambda: SimpleNamespace())

    with TestClient(main_mod.app) as client:
        response = client.post(
            "/bundle-endpoints",
            json={"name": "blocked", "module_import_id": "module-1"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "endpoint_image_not_ready"
    assert response.json()["build_status"] == expected_status
    assert connection.writes == []


def test_endpoint_create_rejects_ready_build_without_local_image_and_unvalidated_revision(
    monkeypatch,
):
    cases = [
        (
            _ImageGateConnection("ready", image_id=None),
            "endpoint_image_not_ready",
            "missing_local_image",
        ),
        (
            _ImageGateConnection("ready", validation_ready=False),
            "endpoint_revision_not_ready",
            "revision_not_ready",
        ),
    ]
    for connection, code, status in cases:
        services = _api_services(connection)
        monkeypatch.setattr(main_mod, "AppServices", lambda settings: services)
        monkeypatch.setattr(main_mod, "get_settings", lambda: SimpleNamespace())
        with TestClient(main_mod.app) as client:
            response = client.post(
                "/bundle-endpoints",
                json={"name": "blocked", "module_import_id": "module-1"},
            )
        assert response.status_code == 409
        assert response.json()["code"] == code
        assert response.json()["build_status"] == status
        assert connection.writes == []


def test_endpoint_create_records_only_managed_target_intent(monkeypatch):
    connection = _ImageGateConnection("ready")
    services = _api_services(connection)
    monkeypatch.setattr(main_mod, "AppServices", lambda settings: services)
    monkeypatch.setattr(main_mod, "get_settings", lambda: SimpleNamespace())

    with TestClient(main_mod.app) as client:
        response = client.post(
            "/bundle-endpoints",
            json={
                "name": "managed",
                "module_import_id": "module-1",
                "pinned_worker_count": 2,
            },
        )

    assert response.status_code == 200
    assert response.json()["deployment"]["legacy_fallback"] is False
    assert [write[0] for write in connection.writes] == ["endpoint", "deployment"]
    deployment = connection.writes[1]
    assert deployment[3:] == ("build-1", "revision-1", 2)


class _RolloutConnection:
    def __init__(self, deployment):
        self.deployment = deployment
        self.executions = []

    async def fetchrow(self, query, *params):
        assert "from endpoint_deployments" in query.lower()
        return dict(self.deployment)

    async def execute(self, query, *params):
        self.executions.append((" ".join(query.strip().lower().split()), params))
        return "UPDATE 1"


def test_module_update_schedules_target_without_replacing_active_revision():
    services = AppServices(SimpleNamespace())
    connection = _RolloutConnection(
        {
            "id": "deployment-1",
            "endpoint_id": "endpoint-1",
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": None,
            "target_revision_id": None,
            "previous_build_id": None,
            "previous_revision_id": None,
            "phase": "ready",
            "desired_replica_count": 1,
            "legacy_fallback": False,
            "rollout_generation": 4,
            "created_at": NOW,
        }
    )

    changed = asyncio.run(
        services._schedule_endpoint_rollout(
            connection,
            endpoint_id="endpoint-1",
            build_id="build-new",
            revision_id="revision-new",
            desired_replica_count=2,
            now=NOW,
        )
    )

    assert changed is True
    query, params = connection.executions[0]
    assert query.startswith("insert into endpoint_deployments")
    assert params[2:8] == (
        "build-old",
        "revision-old",
        "build-new",
        "revision-new",
        None,
        None,
    )
    assert params[8:] == (2, 5, NOW)


class _SchedulerConnection:
    def __init__(self, candidate):
        self.candidate = candidate
        self.executions = []

    def is_closed(self):
        return False

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *params):
        assert "ready_build_id" in query.lower()
        return None if self.candidate is None else dict(self.candidate)

    async def execute(self, query, *params):
        self.executions.append((" ".join(query.strip().lower().split()), params))
        return "UPDATE 1"


def _postgres_store(connection):
    store = PostgresEndpointContainerStore(
        postgres_dsn="postgresql://unused",
        instance_id="test",
        leader_timeout_seconds=5,
    )
    store._connection = connection
    return store


@pytest.mark.parametrize(
    "legacy_fallback,phase", [(True, "legacy_static"), (True, "failed")]
)
def test_scheduler_automatically_starts_initial_legacy_migration(
    legacy_fallback, phase
):
    connection = _SchedulerConnection(
        {
            "id": "deployment-legacy",
            "endpoint_id": "endpoint-1",
            "replica_count": 2,
            "phase": phase,
            "legacy_fallback": legacy_fallback,
            "active_build_id": None,
            "active_revision_id": None,
            "target_build_id": None,
            "target_revision_id": None,
            "previous_build_id": None,
            "previous_revision_id": None,
            "rollout_generation": 0,
            "ready_build_id": "build-new",
            "ready_revision_id": "revision-new",
        }
    )

    assert asyncio.run(_postgres_store(connection).reconcile_deployment_targets()) == 1
    query, params = connection.executions[0]
    assert query.startswith("update endpoint_deployments")
    assert params[1:] == ("build-new", "revision-new", 2)


def test_scheduler_automatically_starts_future_ready_revision_rollout():
    connection = _SchedulerConnection(
        {
            "id": "deployment-current",
            "endpoint_id": "endpoint-1",
            "replica_count": 1,
            "phase": "ready",
            "legacy_fallback": False,
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": None,
            "target_revision_id": None,
            "previous_build_id": "build-older",
            "previous_revision_id": "revision-older",
            "rollout_generation": 7,
            "ready_build_id": "build-new",
            "ready_revision_id": "revision-new",
        }
    )

    assert asyncio.run(_postgres_store(connection).reconcile_deployment_targets()) == 1
    query, params = connection.executions[0]
    assert query.startswith("insert into endpoint_deployments")
    assert params[2:8] == (
        "build-old",
        "revision-old",
        "build-new",
        "revision-new",
        "build-older",
        "revision-older",
    )
    assert params[8:] == (1, 8)


def test_deployment_status_exposes_builds_slots_migration_and_rollback_reason():
    services = AppServices(SimpleNamespace())

    async def deployment(self, endpoint_id):
        return {
            "id": "deployment-1",
            "endpoint_id": endpoint_id,
            "phase": "rollback",
            "legacy_fallback": True,
            "desired_replica_count": 1,
            "rollout_generation": 3,
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": "build-old",
            "target_revision_id": "revision-old",
            "previous_build_id": "build-older",
            "previous_revision_id": "revision-older",
            "failure_reason": "replacement failed readiness",
            "rollout_started_at": NOW,
            "ready_at": None,
            "deadline_at": None,
            "updated_at": NOW,
        }

    async def containers(self, endpoint_id):
        return [
            {
                "container_id": "container-1",
                "container_name": "managed-1",
                "endpoint_id": endpoint_id,
                "deployment_id": "deployment-1",
                "build_id": "build-old",
                "revision_id": "revision-old",
                "slot": 0,
                "worker_id": "worker-1",
                "lifecycle": "draining",
                "failure_reason": None,
                "last_observed_at": NOW.isoformat(),
                "last_heartbeat_at": NOW.isoformat(),
            }
        ]

    async def workers(self):
        return [
            {
                "worker_id": "worker-1",
                "is_live": True,
                "status": "running",
                "assigned_endpoint_id": "endpoint-1",
                "endpoint_deployment_id": "deployment-1",
                "endpoint_slot": 0,
                "endpoint_rollout_generation": 3,
                "warmed_build_id": "build-old",
                "warmed_revision_id": "revision-old",
            }
        ]

    async def build(self, build_id):
        return {
            "id": build_id,
            "status": "ready",
            "failure_reason": None,
        }

    services.get_endpoint_deployment = MethodType(deployment, services)
    services.list_managed_endpoint_containers = MethodType(containers, services)
    services.list_endpoint_worker_registrations = MethodType(workers, services)
    services.get_revision_image_build_status = MethodType(build, services)

    result = asyncio.run(services.get_endpoint_deployment_status("endpoint-1"))

    assert result["migration_state"] == "warming_managed"
    assert result["active"] == {
        "build_id": "build-old",
        "revision_id": "revision-old",
        "build_status": "ready",
        "failure_reason": None,
        "logs_url": "/revision-image-builds/build-old/logs",
    }
    assert result["previous"]["revision_id"] == "revision-older"
    assert result["rollback_reason"] == "replacement failed readiness"
    assert result["slots"][0]["draining"] is True
    assert result["slots"][0]["ready"] is False


def test_rollout_routes_invocations_only_to_active_revision_until_atomic_cutover():
    services = AppServices(SimpleNamespace(endpoint_queue_prefix="endpoint"))

    async def deployment(self, endpoint_id):
        return {
            "id": "deployment-1",
            "endpoint_id": endpoint_id,
            "phase": "rolling",
            "legacy_fallback": False,
            "rollout_generation": 2,
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": "build-new",
            "target_revision_id": "revision-new",
        }

    def worker(build_id, revision_id):
        return {
            "worker_id": f"worker-{build_id}",
            "assigned_endpoint_id": "endpoint-1",
            "endpoint_id": "endpoint-1",
            "is_live": True,
            "status": "listening",
            "execution_mode": "managed_image",
            "desired_build_id": build_id,
            "warmed_build_id": build_id,
            "desired_revision_id": revision_id,
            "warmed_revision_id": revision_id,
            "bundle_path": "/opt/dspy-bundle",
        }

    async def workers(self):
        return {
            "items": [
                worker("build-old", "revision-old"),
                worker("build-new", "revision-new"),
            ]
        }

    services.get_endpoint_deployment = MethodType(deployment, services)
    services.list_endpoint_workers = MethodType(workers, services)

    result = asyncio.run(services.get_endpoint_routing_state("endpoint-1"))

    assert result["ready_targets"] == [
        {
            "execution_mode": "managed_image",
            "build_id": "build-old",
            "revision_id": "revision-old",
            "bundle_path": "/opt/dspy-bundle",
            "queue_name": services._endpoint_queue_name(
                "endpoint-1",
                build_id="build-old",
                revision_id="revision-old",
            ),
        }
    ]


def test_managed_registration_requires_exact_observed_deployment_slot_identity():
    class ObservedSlotConnection:
        async def fetchrow(self, query, *params):
            assert "select c.container_id" in query.lower()
            expected = (
                "deployment-1",
                "endpoint-1",
                0,
                "worker-1",
                "build-1",
                "revision-1",
                3,
            )
            return {"container_id": "container-1"} if params == expected else None

    services = AppServices(SimpleNamespace())
    services.postgres_pool = _Pool(ObservedSlotConnection())

    async def deployment(self, endpoint_id):
        return {
            "id": "deployment-2",
            "endpoint_id": endpoint_id,
            "phase": "rolling",
            "desired_replica_count": 1,
            "rollout_generation": 4,
            "active_build_id": "build-1",
            "active_revision_id": "revision-1",
            "target_build_id": "build-2",
            "target_revision_id": "revision-2",
            "previous_build_id": None,
            "previous_revision_id": None,
        }

    async def build(self, build_id):
        assert build_id == "build-1"
        return {
            "id": build_id,
            "revision_id": "revision-1",
            "status": "ready",
            "image_id": "sha256:ready",
        }

    services.get_endpoint_deployment = MethodType(deployment, services)
    services.get_revision_image_build = MethodType(build, services)
    metadata = {
        "endpoint_id": "endpoint-1",
        "worker_id": "worker-1",
        "endpoint_deployment_id": "deployment-1",
        "endpoint_slot": 0,
        "endpoint_rollout_generation": 3,
        "desired_build_id": "build-1",
        "desired_revision_id": "revision-1",
        "baked_build_id": "build-1",
        "baked_revision_id": "revision-1",
        "bundle_path": "/opt/dspy-bundle",
    }

    accepted = asyncio.run(
        services.validate_managed_endpoint_worker_identity(
            metadata, worker_id="worker-1"
        )
    )
    assert accepted["deployment_id"] == "deployment-1"
    assert accepted["slot"] == 0
    assert accepted["rollout_generation"] == 3

    stale = dict(metadata, endpoint_rollout_generation=2)
    with pytest.raises(ValueError, match="not an observed deployment slot"):
        asyncio.run(
            services.validate_managed_endpoint_worker_identity(
                stale, worker_id="worker-1"
            )
        )
