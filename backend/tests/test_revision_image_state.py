import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.revision_images import (
    MAX_BUILD_LOG_BYTES,
    build_endpoint_deployment_payload,
    build_managed_container_payload,
    build_revision_image_payload,
    validate_endpoint_deployment_transition,
    validate_managed_container_transition,
    validate_revision_image_build_transition,
)


@contextmanager
def _service_module():
    try:
        from app import services as module
    except ModuleNotFoundError:
        asyncpg = ModuleType("asyncpg")
        asyncpg.Pool = object
        asyncpg.create_pool = None

        cryptography = ModuleType("cryptography")
        cryptography.__path__ = []
        fernet = ModuleType("cryptography.fernet")

        class Fernet:
            pass

        class InvalidToken(Exception):
            pass

        fernet.Fernet = Fernet
        fernet.InvalidToken = InvalidToken

        httpx = ModuleType("httpx")
        httpx.AsyncClient = object
        redis = ModuleType("redis")
        redis.__path__ = []
        redis_asyncio = ModuleType("redis.asyncio")
        redis_asyncio.Redis = object

        config = ModuleType("app.config")
        config.Settings = object
        validator = ModuleType("app.validator")
        validator.read_bundle_metadata = None
        validator.validate_bundle = None

        stubs = {
            "asyncpg": asyncpg,
            "cryptography": cryptography,
            "cryptography.fernet": fernet,
            "httpx": httpx,
            "redis": redis,
            "redis.asyncio": redis_asyncio,
            "app.config": config,
            "app.validator": validator,
        }
        module_name = "app._revision_image_contract_services"
        spec = importlib.util.spec_from_file_location(module_name, BACKEND_ROOT / "app" / "services.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stubs):
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                yield module
            finally:
                sys.modules.pop(module_name, None)
        return
    yield module


class _SchemaConnection:
    def __init__(self) -> None:
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        self.endpoints = {
            "endpoint-existing": {
                "id": "endpoint-existing",
                "pinned_worker_count": 3,
                "created_at": now,
                "updated_at": now,
            }
        }
        self.revisions = {"revision-existing": {"id": "revision-existing"}}
        self.deployments: dict[tuple[str, int], dict] = {}
        self.queries: list[str] = []

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        self.queries.append(normalized)
        if normalized.startswith("insert into endpoint_deployments") and "from bundle_endpoints e" in normalized:
            for endpoint in self.endpoints.values():
                key = (endpoint["id"], 0)
                self.deployments.setdefault(
                    key,
                    {
                        "id": f"legacy-{endpoint['id']}",
                        "endpoint_id": endpoint["id"],
                        "phase": "legacy_static",
                        "desired_replica_count": max(1, endpoint["pinned_worker_count"]),
                        "rollout_generation": 0,
                    },
                )
        elif normalized.startswith("insert into endpoint_deployments"):
            key = (params[1], 0)
            self.deployments.setdefault(
                key,
                {
                    "id": params[0],
                    "endpoint_id": params[1],
                    "phase": "legacy_static",
                    "desired_replica_count": params[2],
                    "rollout_generation": 0,
                },
            )
        elif normalized.startswith("update endpoint_deployments set desired_replica_count"):
            deployment = self.deployments.get((params[0], 0))
            if deployment is not None and deployment["phase"] == "legacy_static":
                deployment["desired_replica_count"] = params[1]
        return "OK"

    async def fetch(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        self.queries.append(normalized)
        if "from revision_image_builds" in normalized:
            return list(getattr(self, "build_rows", []))
        if "from endpoint_deployments" in normalized:
            return list(getattr(self, "deployment_rows", []))
        if "from managed_endpoint_containers" in normalized:
            return list(getattr(self, "container_rows", []))
        return []

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        self.queries.append(normalized)
        if "from revision_image_builds" in normalized:
            for row in getattr(self, "build_rows", []):
                if row["id"] == params[0]:
                    return row
        return None


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


def _services(module, connection):
    services = module.AppServices(SimpleNamespace())
    services.postgres_pool = _Pool(connection)
    return services


def test_startup_schema_is_idempotent_and_backfills_existing_endpoints_without_rewriting_source_rows():
    async def scenario():
        with _service_module() as module:
            connection = _SchemaConnection()
            services = _services(module, connection)
            endpoint_ids_before = set(connection.endpoints)
            revision_ids_before = set(connection.revisions)

            await services.init_db()
            await services.init_db()

            assert set(connection.endpoints) == endpoint_ids_before
            assert set(connection.revisions) == revision_ids_before
            assert connection.deployments == {
                ("endpoint-existing", 0): {
                    "id": "legacy-endpoint-existing",
                    "endpoint_id": "endpoint-existing",
                    "phase": "legacy_static",
                    "desired_replica_count": 3,
                    "rollout_generation": 0,
                }
            }

            schema = "\n".join(connection.queries)
            assert "create table if not exists revision_image_builds" in schema
            assert "unique (revision_id, generation)" in schema
            assert "source_snapshot_path text not null" in schema
            assert "source_content_digest text not null" in schema
            assert "uq_revision_image_builds_active_revision" in schema
            assert "idx_revision_image_builds_fifo" in schema
            assert "idx_revision_image_builds_claim_recovery" in schema
            assert f"octet_length(build_log) <= {MAX_BUILD_LOG_BYTES}" in schema
            assert "ready revision image build identity is immutable" in schema
            assert "create table if not exists endpoint_deployments" in schema
            assert "references revision_image_builds(id, revision_id) match full on delete restrict" in schema
            assert "create table if not exists managed_endpoint_containers" in schema
            assert "endpoint_id text not null references bundle_endpoints(id) on delete restrict" in schema

    asyncio.run(scenario())


def test_legacy_deployment_intent_is_created_once_and_tracks_replica_updates():
    async def scenario():
        with _service_module() as module:
            connection = _SchemaConnection()
            services = _services(module, connection)
            now = datetime(2026, 9, 25, tzinfo=timezone.utc)

            await services._ensure_legacy_endpoint_deployment(
                connection,
                endpoint_id="endpoint-new",
                desired_replica_count=2,
                created_at=now,
                updated_at=now,
            )
            await services._ensure_legacy_endpoint_deployment(
                connection,
                endpoint_id="endpoint-new",
                desired_replica_count=99,
                created_at=now,
                updated_at=now,
            )
            await services._update_legacy_endpoint_deployment_replica_count(
                connection,
                endpoint_id="endpoint-new",
                desired_replica_count=4,
                updated_at=now,
            )

            assert connection.deployments[("endpoint-new", 0)]["desired_replica_count"] == 4
            assert len([key for key in connection.deployments if key[0] == "endpoint-new"]) == 1

    asyncio.run(scenario())


def test_build_deployment_and_container_state_are_independently_queryable():
    async def scenario():
        with _service_module() as module:
            connection = _SchemaConnection()
            now = datetime(2026, 9, 25, tzinfo=timezone.utc)
            connection.build_rows = [
                {
                    "id": "build-2",
                    "revision_id": "revision-1",
                    "generation": 2,
                    "source_commit": "abc123",
                    "source_snapshot_path": "/snapshots/sha256-aaa.tar",
                    "source_content_digest": "sha256:aaa",
                    "local_tag": "dspy-trainer/revision:revision-1-g2",
                    "image_id": "sha256:image",
                    "image_digest": None,
                    "base_image_id": "sha256:base",
                    "status": "ready",
                    "attempt": 1,
                    "available_at": now,
                    "claim_owner": None,
                    "claim_expires_at": None,
                    "build_log": "built",
                    "failure_reason": None,
                    "retry_of_build_id": "build-1",
                    "queued_at": now,
                    "started_at": now,
                    "finished_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
            connection.deployment_rows = [
                {
                    "id": "deployment-3",
                    "endpoint_id": "endpoint-1",
                    "active_build_id": "build-1",
                    "active_revision_id": "revision-1",
                    "target_build_id": "build-2",
                    "target_revision_id": "revision-2",
                    "previous_build_id": "build-0",
                    "previous_revision_id": "revision-0",
                    "phase": "rolling",
                    "desired_replica_count": 3,
                    "rollout_generation": 3,
                    "deadline_at": now,
                    "failure_reason": None,
                    "rollout_started_at": now,
                    "ready_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
            connection.container_rows = [
                {
                    "container_id": "sha256:container",
                    "container_name": "endpoint-1-g3-s0",
                    "endpoint_id": "endpoint-1",
                    "deployment_id": "deployment-3",
                    "build_id": "build-2",
                    "revision_id": "revision-2",
                    "slot": 0,
                    "lifecycle": "ready",
                    "last_observed_at": now,
                    "last_heartbeat_at": now,
                    "started_at": now,
                    "stopped_at": None,
                    "failure_reason": None,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
            services = _services(module, connection)

            builds = await services.list_revision_image_builds("revision-1")
            deployment = await services.get_endpoint_deployment("endpoint-1")
            containers = await services.list_managed_endpoint_containers("endpoint-1")

            assert builds[0]["status"] == "ready"
            assert builds[0]["source_content_digest"] == "sha256:aaa"
            assert deployment["active_revision_id"] == "revision-1"
            assert deployment["target_revision_id"] == "revision-2"
            assert deployment["previous_revision_id"] == "revision-0"
            assert containers[0]["build_id"] == "build-2"
            assert containers[0]["last_heartbeat_at"] == now.isoformat()

    asyncio.run(scenario())


def test_state_transition_contracts_reject_generation_reuse_and_invalid_rollout_jumps():
    assert validate_revision_image_build_transition("queued", "building") == "building"
    assert validate_revision_image_build_transition("building", "ready") == "ready"
    assert validate_revision_image_build_transition("ready", "superseded") == "superseded"
    assert validate_endpoint_deployment_transition("rolling", "rollback") == "rollback"
    assert validate_endpoint_deployment_transition("rollback", "ready") == "ready"
    assert validate_managed_container_transition("ready", "draining") == "draining"

    with pytest.raises(ValueError, match="ready -> building"):
        validate_revision_image_build_transition("ready", "building")
    with pytest.raises(ValueError, match="failed -> queued"):
        validate_revision_image_build_transition("failed", "queued")
    with pytest.raises(ValueError, match="legacy_static -> ready"):
        validate_endpoint_deployment_transition("legacy_static", "ready")
    with pytest.raises(ValueError, match="removed -> starting"):
        validate_managed_container_transition("removed", "starting")


def test_payload_builders_serialize_timestamps_and_keep_mixed_revision_pointers():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    build = build_revision_image_payload(
        {
            "id": "build-1",
            "revision_id": "revision-1",
            "generation": 1,
            "status": "queued",
            "attempt": 0,
            "available_at": now,
            "queued_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    deployment = build_endpoint_deployment_payload(
        {
            "id": "deployment-2",
            "endpoint_id": "endpoint-1",
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": "build-new",
            "target_revision_id": "revision-new",
            "previous_build_id": "build-older",
            "previous_revision_id": "revision-older",
            "phase": "rolling",
            "desired_replica_count": 2,
            "rollout_generation": 2,
            "created_at": now,
            "updated_at": now,
        }
    )
    container = build_managed_container_payload(
        {
            "container_id": "container-1",
            "container_name": "managed-1",
            "endpoint_id": "endpoint-1",
            "deployment_id": "deployment-2",
            "build_id": "build-new",
            "revision_id": "revision-new",
            "slot": 0,
            "lifecycle": "starting",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert build["available_at"] == now.isoformat()
    assert deployment["active_revision_id"] == "revision-old"
    assert deployment["target_revision_id"] == "revision-new"
    assert deployment["previous_revision_id"] == "revision-older"
    assert container["created_at"] == now.isoformat()
