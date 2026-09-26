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
                    params[5],
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
    assert deployment[3:] == ("build-1", "revision-1", "module-1", 2)


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
            "active_module_import_id": "module-old",
            "target_build_id": None,
            "target_revision_id": None,
            "target_module_import_id": None,
            "previous_build_id": None,
            "previous_revision_id": None,
            "previous_module_import_id": None,
            "phase": "ready",
            "failed_build_id": None,
            "failed_revision_id": None,
            "failed_module_import_id": None,
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
            target_module_import_id="module-new",
            desired_replica_count=2,
            now=NOW,
        )
    )

    assert changed is True
    query, params = connection.executions[0]
    assert query.startswith("insert into endpoint_deployments")
    assert params[2:11] == (
        "build-old",
        "revision-old",
        "module-old",
        "build-new",
        "revision-new",
        "module-new",
        None,
        None,
        None,
    )
    assert params[11:] == (2, 5, NOW)


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
            "active_module_import_id": None,
            "target_revision_id": None,
            "previous_build_id": None,
            "target_module_import_id": None,
            "previous_revision_id": None,
            "rollout_generation": 0,
            "previous_module_import_id": None,
            "failed_build_id": None,
            "ready_module_import_id": "module-new",
            "ready_build_id": "build-new",
            "ready_revision_id": "revision-new",
        }
    )

    assert asyncio.run(_postgres_store(connection).reconcile_deployment_targets()) == 1
    query, params = connection.executions[0]
    assert query.startswith("update endpoint_deployments")
    assert params[1:] == ("build-new", "revision-new", "module-new", 2)


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
            "active_module_import_id": "module-old",
            "target_build_id": None,
            "target_revision_id": None,
            "target_module_import_id": None,
            "previous_build_id": "build-older",
            "previous_revision_id": "revision-older",
            "previous_module_import_id": "module-older",
            "failed_build_id": None,
            "rollout_generation": 7,
            "ready_build_id": "build-new",
            "ready_revision_id": "revision-new",
            "ready_module_import_id": "module-new",
        }
    )

    assert asyncio.run(_postgres_store(connection).reconcile_deployment_targets()) == 1
    query, params = connection.executions[0]
    assert query.startswith("insert into endpoint_deployments")
    assert params[2:11] == (
        "build-old",
        "revision-old",
        "module-old",
        "build-new",
        "revision-new",
        "module-new",
        "build-older",
        "revision-older",
        "module-older",
    )
    assert params[11:] == (1, 8)
class _FailedRolloutStateConnection:
    def __init__(self):
        self.deployment = {
            "id": "deployment-1",
            "endpoint_id": "endpoint-1",
            "replica_count": 1,
            "phase": "rolling",
            "legacy_fallback": False,
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "active_module_import_id": "module-old",
            "target_build_id": "build-failed",
            "target_revision_id": "revision-failed",
            "target_module_import_id": "module-new",
            "previous_build_id": None,
            "previous_revision_id": None,
            "previous_module_import_id": None,
            "failed_build_id": None,
            "failed_revision_id": None,
            "failed_module_import_id": None,
            "rollout_generation": 1,
        }
        self.ready_builds = [
            ("build-failed", "revision-failed", "module-new"),
        ]
        self.executions = []

    def is_closed(self):
        return False

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *params):
        del params
        normalized = " ".join(query.strip().lower().split())
        assert "ready.id is distinct from d.failed_build_id" in normalized
        assert "coalesce(d.failed_module_import_id, e.module_import_id)" in normalized
        assert "d.phase in ('legacy_static', 'ready', 'failed')" in normalized
        if self.deployment["phase"] not in {"legacy_static", "ready", "failed"}:
            return None
        build_id, revision_id, module_id = self.ready_builds[-1]
        if build_id in {
            self.deployment["active_build_id"],
            self.deployment["target_build_id"],
            self.deployment["failed_build_id"],
        }:
            return None
        return {
            **self.deployment,
            "ready_build_id": build_id,
            "ready_revision_id": revision_id,
            "ready_module_import_id": module_id,
        }

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        self.executions.append((normalized, params))
        if normalized.startswith("update endpoint_deployments set failed_build_id"):
            deployment = self.deployment
            deployment["failed_build_id"] = deployment["target_build_id"]
            deployment["failed_revision_id"] = deployment["target_revision_id"]
            deployment["failed_module_import_id"] = deployment["target_module_import_id"]
            deployment["phase"] = "rollback"
            deployment["target_build_id"] = deployment["active_build_id"]
            deployment["target_revision_id"] = deployment["active_revision_id"]
            deployment["target_module_import_id"] = deployment["active_module_import_id"]
            return "UPDATE 1"
        if normalized.startswith("update endpoint_deployments set target_build_id = null"):
            self.deployment.update(
                phase="ready",
                target_build_id=None,
                target_revision_id=None,
                target_module_import_id=None,
            )
            return "UPDATE 1"
        if normalized.startswith("insert into endpoint_deployments"):
            return "INSERT 1"
        raise AssertionError(f"unexpected execute SQL: {normalized}")


def test_failed_build_is_suppressed_across_cycles_and_store_restart_until_newer_build():
    async def scenario():
        connection = _FailedRolloutStateConnection()
        store = _postgres_store(connection)
        await store.fail_or_rollback_deployment("deployment-1", "readiness failed")
        assert connection.deployment["failed_build_id"] == "build-failed"
        assert connection.deployment["failed_revision_id"] == "revision-failed"
        assert connection.deployment["failed_module_import_id"] == "module-new"
        await store.complete_deployment("deployment-1", rolled_back=True)

        assert await store.reconcile_deployment_targets() == 0
        restarted_store = _postgres_store(connection)
        assert await restarted_store.reconcile_deployment_targets() == 0
        assert connection.deployment["failed_build_id"] == "build-failed"

        connection.ready_builds.append(
            ("build-newer", "revision-newer", "module-new")
        )
        assert await restarted_store.reconcile_deployment_targets() == 1
        query, params = connection.executions[-1]
        assert query.startswith("insert into endpoint_deployments")
        assert params[5:8] == ("build-newer", "revision-newer", "module-new")

    asyncio.run(scenario())


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
            "active_module_import_id": "module-old",
            "target_build_id": "build-old",
            "target_revision_id": "revision-old",
            "target_module_import_id": "module-old",
            "previous_build_id": "build-older",
            "previous_revision_id": "revision-older",
            "previous_module_import_id": "module-older",
            "failed_build_id": "build-new",
            "failed_revision_id": "revision-new",
            "failed_module_import_id": "module-new",
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
        "module_import_id": "module-old",
        "build_status": "ready",
        "failure_reason": None,
        "logs_url": "/revision-image-builds/build-old/logs",
    }
    assert result["previous"]["revision_id"] == "revision-older"
    assert result["failed_target"]["build_id"] == "build-new"
    assert result["failed_target"]["module_import_id"] == "module-new"
    assert result["rollback_reason"] == "replacement failed readiness"
    assert result["slots"][0]["draining"] is True
    assert result["slots"][0]["ready"] is False
class _PatchCutoverConnection:
    def __init__(self, *, legacy):
        self.endpoint = {
            "id": "endpoint-1",
            "module_import_id": "module-old",
            "lm_profile_id": None,
            "pinned_worker_count": 1,
            "name": "Endpoint",
            "key_preview": "abc123",
            "created_at": NOW,
            "updated_at": NOW,
            "module_bundle_name": "old-bundle",
            "lm_profile_name": None,
        }
        self.deployment = {
            "id": "deployment-old",
            "endpoint_id": "endpoint-1",
            "active_build_id": None if legacy else "build-old",
            "active_revision_id": None if legacy else "revision-old",
            "active_module_import_id": "module-old",
            "target_build_id": None,
            "target_revision_id": None,
            "target_module_import_id": None,
            "previous_build_id": None,
            "previous_revision_id": None,
            "previous_module_import_id": None,
            "failed_build_id": None,
            "failed_revision_id": None,
            "failed_module_import_id": None,
            "phase": "legacy_static" if legacy else "ready",
            "desired_replica_count": 1,
            "legacy_fallback": legacy,
            "rollout_generation": 0,
            "created_at": NOW,
        }

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("select m.id as module_id"):
            assert params == ("module-new",)
            return {
                "module_id": "module-new",
                "current_revision_id": "revision-new",
                "validation_status": "passed",
                "validation_revision_id": "revision-new",
                "ready_build_id": "build-new",
                "ready_generation": 2,
                "ready_image_id": "sha256:new",
                "latest_build_id": "build-new",
                "latest_generation": 2,
                "latest_build_status": "ready",
                "latest_image_id": "sha256:new",
                "latest_failure_reason": None,
            }
        if normalized.startswith("select id, generation, image_id"):
            return {"id": "build-new", "generation": 2, "image_id": "sha256:new"}
        if normalized.startswith("update bundle_endpoints"):
            assert "module_import_id =" not in normalized
            self.endpoint.update(
                name=params[1],
                lm_profile_id=params[2],
                pinned_worker_count=params[3],
                updated_at=params[4],
            )
            return dict(self.endpoint)
        if "from endpoint_deployments" in normalized:
            return dict(self.deployment)
        if "from bundle_endpoints e" in normalized:
            return dict(self.endpoint)
        raise AssertionError(f"unexpected fetchrow SQL: {normalized}")

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("insert into endpoint_deployments"):
            self.deployment.update(
                id=params[0],
                active_build_id=params[2],
                active_revision_id=params[3],
                active_module_import_id=params[4],
                target_build_id=params[5],
                target_revision_id=params[6],
                target_module_import_id=params[7],
                phase="pending",
                legacy_fallback=False,
                rollout_generation=params[12],
            )
            return "INSERT 1"
        if normalized.startswith("update endpoint_deployments"):
            self.deployment.update(
                target_build_id=params[1],
                target_revision_id=params[2],
                target_module_import_id=params[3],
                desired_replica_count=params[4],
                phase="pending",
            )
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute SQL: {normalized}")


@pytest.mark.parametrize("execution_mode", ["legacy_static", "managed_image"])
def test_patch_keeps_old_module_routing_secrets_and_trace_until_cutover(
    monkeypatch, execution_mode
):
    legacy = execution_mode == "legacy_static"
    connection = _PatchCutoverConnection(legacy=legacy)
    services = AppServices(
        SimpleNamespace(endpoint_queue_prefix="endpoint", mlflow_tracking_uri="")
    )
    services.postgres_pool = _Pool(connection)
    services.connect_backend = _no_op
    services.disconnect = _no_op
    services.reconcile_endpoint_worker_assignments = _no_op

    async def deployment(endpoint_id):
        assert endpoint_id == "endpoint-1"
        return dict(connection.deployment)

    async def deployment_status(endpoint_id):
        return {
            "endpoint_id": endpoint_id,
            "phase": connection.deployment["phase"],
            "active": {
                "build_id": connection.deployment["active_build_id"],
                "module_import_id": connection.deployment["active_module_import_id"],
            },
            "target": {
                "build_id": connection.deployment["target_build_id"],
                "module_import_id": connection.deployment["target_module_import_id"],
            },
        }

    def worker(mode, build_id, revision_id):
        return {
            "worker_id": f"worker-{mode}-{build_id or revision_id}",
            "assigned_endpoint_id": "endpoint-1",
            "endpoint_id": "endpoint-1",
            "is_live": True,
            "status": "listening",
            "execution_mode": mode,
            "desired_build_id": build_id,
            "warmed_build_id": build_id,
            "desired_revision_id": revision_id,
            "warmed_revision_id": revision_id,
            "bundle_path": "/opt/dspy-bundle" if mode == "managed_image" else None,
        }

    async def workers():
        old = (
            worker("legacy_static", None, "revision-old")
            if legacy
            else worker("managed_image", "build-old", "revision-old")
        )
        return {"items": [old, worker("managed_image", "build-new", "revision-new")]}

    async def execution_state(module_id, fallback_bundle_path=None):
        del fallback_bundle_path
        assert module_id == "module-old"
        return {
            "bundle_path": "/tmp/old-bundle",
            "bundle_revision_id": "revision-old",
            "commit_sha": "old-commit",
        }

    runtime_modules = []

    async def runtime_environment(module_id):
        runtime_modules.append(module_id)
        return {"MODULE_SECRET": f"secret-for-{module_id}"}

    async def registration(worker_id):
        return {
            "is_live": True,
            "runtime_metadata": {
                "worker_id": worker_id,
                "endpoint_deployment_id": connection.deployment["id"],
                "endpoint_slot": 0,
                "endpoint_rollout_generation": connection.deployment["rollout_generation"],
                "baked_build_id": "build-old",
                "baked_revision_id": "revision-old",
            },
        }

    async def validate(metadata, *, worker_id=None):
        del metadata, worker_id
        return {
            "bundle_path": "/opt/dspy-bundle",
            "build_id": "build-old",
            "revision_id": "revision-old",
        }

    events = []

    async def publish(invocation_id, event, payload):
        events.append((invocation_id, event, payload))

    services.get_endpoint_deployment = deployment
    services.get_endpoint_deployment_status = deployment_status
    services.list_endpoint_workers = workers
    services.resolve_module_execution_state = execution_state
    services.get_module_runtime_environment = runtime_environment
    services.ensure_bundle_requirements_installed = _no_op
    services._get_endpoint_worker_registration = registration
    services.validate_managed_endpoint_worker_identity = validate
    services.publish_endpoint_invocation_event = publish
    monkeypatch.setattr(main_mod, "AppServices", lambda settings: services)
    monkeypatch.setattr(main_mod, "get_settings", lambda: SimpleNamespace())

    captured = {}

    def invoke_bundle(bundle_path, input_payload, lm_profile, runtime_env):
        del lm_profile
        captured["bundle_path"] = bundle_path
        captured["runtime_env"] = runtime_env
        return {"answer": input_payload["question"]}

    def traced(operation, *, tracking_uri, input_payload, attributes):
        del tracking_uri, input_payload
        captured["attributes"] = attributes
        return operation(), "trace-old"

    monkeypatch.setattr("app.executor.module_runner.invoke_bundle", invoke_bundle)
    monkeypatch.setattr("app.services._run_endpoint_invocation_with_mlflow", traced)

    with TestClient(main_mod.app) as client:
        response = client.patch(
            "/bundle-endpoints/endpoint-1",
            json={"module_import_id": "module-new"},
        )
    assert response.status_code == 200
    assert response.json()["module_import_id"] == "module-old"
    assert connection.endpoint["module_import_id"] == "module-old"
    assert connection.deployment["target_module_import_id"] == "module-new"

    routing = asyncio.run(services.get_endpoint_routing_state("endpoint-1"))
    assert routing["ready_targets"] == [
        {
            "execution_mode": execution_mode,
            "build_id": None if legacy else "build-old",
            "revision_id": "revision-old",
            "bundle_path": None if legacy else "/opt/dspy-bundle",
            "queue_name": services._endpoint_queue_name(
                "endpoint-1",
                build_id=None if legacy else "build-old",
                revision_id=None if legacy else "revision-old",
            ),
        }
    ]

    asyncio.run(
        services.run_endpoint_invocation_job(
            "invocation-old",
            "endpoint-1",
            {"question": "still old"},
            "worker-old",
            stream=False,
            execution_mode=execution_mode,
            build_id=None if legacy else "build-old",
            revision_id="revision-old",
            bundle_path=None if legacy else "/opt/dspy-bundle",
        )
    )
    assert runtime_modules == ["module-old"]
    assert captured["runtime_env"] == {"MODULE_SECRET": "secret-for-module-old"}
    assert captured["attributes"]["module_import_id"] == "module-old"
    assert captured["attributes"]["bundle_revision_id"] == "revision-old"
    assert events == [("invocation-old", "final", {"answer": "still old"})]


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
                True,
            )
            return {"container_id": "container-1", "lifecycle": "ready"} if params == expected else None

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
def test_claimed_draining_worker_heartbeats_completes_but_cannot_reclaim_or_restart(monkeypatch):
    class ClaimEligibilityConnection:
        def __init__(self):
            self.lifecycle = "ready"
            self.registration = {
                "worker_id": "worker-1",
                "runtime_instance_id": "runtime-original",
                "status": "running",
                "assigned_endpoint_id": "endpoint-1",
                "task_id": "invocation-1",
                "last_seen_at": NOW,
                "heartbeat_expires_at": NOW.replace(year=2100),
                "hostname": "host-1",
                "pid": 101,
                "runtime_metadata": dict(metadata),
                "last_error": None,
                "created_at": NOW,
                "updated_at": NOW,
            }

        def is_closed(self):
            return False

        async def fetchrow(self, query, *params):
            normalized = " ".join(query.strip().lower().split())
            if normalized.startswith("select c.container_id"):
                allow_draining = bool(params[7])
                eligible = self.lifecycle == "ready" or (
                    allow_draining and self.lifecycle == "draining"
                )
                return (
                    {"container_id": "container-1", "lifecycle": self.lifecycle}
                    if eligible
                    else None
                )
            if normalized.startswith(
                "select worker_id, runtime_instance_id, status, assigned_endpoint_id"
            ):
                return (
                    dict(self.registration)
                    if params[0] == self.registration["worker_id"]
                    else None
                )
            if normalized.startswith("update endpoint_worker_registrations set status = $3"):
                if (
                    params[0] != self.registration["worker_id"]
                    or params[1] != self.registration["runtime_instance_id"]
                    or params[10] != self.registration["task_id"]
                ):
                    return None
                self.registration.update(
                    status=params[2],
                    task_id=params[3],
                    last_seen_at=params[4],
                    heartbeat_expires_at=params[5],
                    hostname=params[6],
                    pid=params[7],
                    runtime_metadata=__import__("json").loads(params[8]),
                    last_error=params[9],
                    updated_at=params[4],
                )
                return dict(self.registration)
            if normalized.startswith(
                "update endpoint_worker_registrations set status = 'running'"
            ):
                return None
            raise AssertionError(f"unexpected fetchrow SQL: {normalized}")

        async def execute(self, query, *params):
            normalized = " ".join(query.strip().lower().split())
            assert normalized.startswith("with blocked as")
            assert params == ("worker-1",)
            self.lifecycle = "draining"
            self.registration["assigned_endpoint_id"] = None
            return "UPDATE 1"

    metadata = {
        "execution_mode": "managed_image",
        "endpoint_id": "endpoint-1",
        "worker_id": "worker-1",
        "endpoint_deployment_id": "deployment-old",
        "endpoint_slot": 0,
        "endpoint_rollout_generation": 3,
        "desired_build_id": "build-old",
        "desired_revision_id": "revision-old",
        "baked_build_id": "build-old",
        "baked_revision_id": "revision-old",
        "bundle_path": "/opt/dspy-bundle",
    }
    connection = ClaimEligibilityConnection()
    services = AppServices(
        SimpleNamespace(
            endpoint_worker_heartbeat_ttl_seconds=30,
            mlflow_tracking_uri="",
        )
    )
    services.postgres_pool = _Pool(connection)

    async def deployment(self, endpoint_id):
        return {
            "id": "deployment-current",
            "endpoint_id": endpoint_id,
            "phase": "rolling",
            "desired_replica_count": 1,
            "rollout_generation": 4,
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": "build-new",
            "target_revision_id": "revision-new",
            "previous_build_id": None,
            "previous_revision_id": None,
        }

    async def build(self, build_id):
        assert build_id == "build-old"
        return {
            "id": "build-old",
            "revision_id": "revision-old",
            "status": "ready",
            "image_id": "sha256:old",
        }

    services.get_endpoint_deployment = MethodType(deployment, services)
    services.get_revision_image_build = MethodType(build, services)

    async def endpoint(endpoint_id):
        return {
            "id": endpoint_id,
            "module_import_id": "module-old",
            "lm_profile_id": None,
            "name": "Endpoint",
        }

    invocation_events = []

    async def runtime_environment(module_id):
        assert module_id == "module-old"
        return {"MODULE_SECRET": "old-secret"}

    async def publish(invocation_id, event, payload):
        invocation_events.append((invocation_id, event, payload))

    services.get_bundle_endpoint = endpoint
    services.get_module_runtime_environment = runtime_environment
    services.publish_endpoint_invocation_event = publish

    def invoke_bundle(bundle_path, input_payload, lm_profile, runtime_env):
        assert bundle_path == "/opt/dspy-bundle"
        assert runtime_env == {"MODULE_SECRET": "old-secret"}
        return {"answer": input_payload["question"]}

    def traced(operation, **kwargs):
        del kwargs
        return operation(), "trace-draining"

    monkeypatch.setattr("app.executor.module_runner.invoke_bundle", invoke_bundle)
    monkeypatch.setattr("app.services._run_endpoint_invocation_with_mlflow", traced)
    accepted = asyncio.run(
        services.validate_managed_endpoint_worker_identity(
            metadata, worker_id="worker-1", require_claim_eligible=True
        )
    )
    assert accepted["container_lifecycle"] == "ready"

    store = _postgres_store(connection)
    asyncio.run(store.block_worker_claims("worker-1"))
    assert connection.lifecycle == "draining"
    assert connection.registration["assigned_endpoint_id"] is None

    heartbeat = asyncio.run(
        services.heartbeat_endpoint_worker(
            "worker-1",
            runtime_instance_id="runtime-original",
            status="running",
            task_id="invocation-1",
            runtime_metadata=metadata,
            now=NOW.replace(day=2),
        )
    )
    assert heartbeat is not None
    assert heartbeat["task_id"] == "invocation-1"
    assert heartbeat["assigned_endpoint_id"] is None

    asyncio.run(
        services.run_endpoint_invocation_job(
            "invocation-1",
            "endpoint-1",
            {"question": "finish"},
            "worker-1",
            stream=False,
            execution_mode="managed_image",
            build_id="build-old",
            revision_id="revision-old",
            bundle_path="/opt/dspy-bundle",
        )
    )
    assert invocation_events == [
        ("invocation-1", "final", {"answer": "finish"})
    ]


    asyncio.run(
        services.run_endpoint_invocation_job(
            "invocation-2",
            "endpoint-1",
            {"question": "must not start"},
            "worker-1",
            stream=False,
            execution_mode="managed_image",
            build_id="build-old",
            revision_id="revision-old",
            bundle_path="/opt/dspy-bundle",
        )
    )
    assert invocation_events[-1][0:2] == ("invocation-2", "error")
    assert "only finish its claimed invocation" in invocation_events[-1][2]["error"]
    changed_task = asyncio.run(
        services.heartbeat_endpoint_worker(
            "worker-1",
            runtime_instance_id="runtime-original",
            status="running",
            task_id="invocation-2",
            runtime_metadata=metadata,
            now=NOW.replace(day=3),
        )
    )
    assert changed_task is None
    assert connection.registration["task_id"] == "invocation-1"

    stale = asyncio.run(
        services.heartbeat_endpoint_worker(
            "worker-1",
            runtime_instance_id="runtime-restarted",
            status="running",
            task_id="invocation-2",
            runtime_metadata=metadata,
            now=NOW.replace(day=3),
        )
    )
    assert stale is None
    assert connection.registration["task_id"] == "invocation-1"

    reclaimed = asyncio.run(
        services.claim_endpoint_worker_task(
            worker_id="worker-1",
            endpoint_id="endpoint-1",
            task_id="invocation-2",
            execution_mode="managed_image",
            build_id="build-old",
            revision_id="revision-old",
            bundle_path="/opt/dspy-bundle",
        )
    )
    assert reclaimed is False

    with pytest.raises(ValueError, match="not an observed deployment slot"):
        asyncio.run(
            services.register_endpoint_worker(
                worker_id="worker-1",
                runtime_instance_id="runtime-restarted",
                status="listening",
                runtime_metadata=metadata,
                now=NOW.replace(day=3),
            )
        )

    completed = asyncio.run(
        services.heartbeat_endpoint_worker(
            "worker-1",
            runtime_instance_id="runtime-original",
            status="listening",
            task_id=None,
            runtime_metadata=metadata,
            now=NOW.replace(day=4),
        )
    )
    assert completed is not None
    assert completed["task_id"] is None
    assert completed["assigned_endpoint_id"] is None

def test_successful_cutover_promotes_build_and_module_in_one_statement():
    class CutoverConnection:
        def __init__(self):
            self.query = None
            self.params = None

        def is_closed(self):
            return False

        async def execute(self, query, *params):
            self.query = " ".join(query.strip().lower().split())
            self.params = params
            return "UPDATE 1"

    connection = CutoverConnection()
    asyncio.run(
        _postgres_store(connection).complete_deployment(
            "deployment-1", rolled_back=False
        )
    )
    assert connection.params == ("deployment-1",)
    assert connection.query.startswith("with promoted as")
    assert "active_module_import_id = target_module_import_id" in connection.query
    assert "update bundle_endpoints e set module_import_id = promoted.active_module_import_id" in connection.query
