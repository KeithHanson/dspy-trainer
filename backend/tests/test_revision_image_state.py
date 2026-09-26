import asyncio
from contextlib import contextmanager
from copy import deepcopy
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
            matches = [
                deployment
                for (endpoint_id, _), deployment in self.deployments.items()
                if endpoint_id == params[0]
            ]
            if matches:
                deployment = max(matches, key=lambda item: item["rollout_generation"])
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


class _ConstraintViolation(ValueError):
    pass


_BUILD_IDENTITY_FIELDS = (
    "id",
    "revision_id",
    "generation",
    "source_commit",
    "source_snapshot_path",
    "source_content_digest",
    "local_tag",
    "base_image_id",
    "retry_of_build_id",
    "queued_at",
    "created_at",
)


class _RevisionImageConstraintFake:
    def __init__(self, schema: str) -> None:
        required_contracts = (
            "if new.id is distinct from old.id",
            "foreign key (retry_of_build_id, revision_id)",
            "status = 'building' and claim_owner is not null and claim_expires_at is not null",
            "foreign key (deployment_id, endpoint_id)",
        )
        missing = [contract for contract in required_contracts if contract not in schema]
        if missing:
            raise AssertionError(f"schema contract missing: {missing}")
        self.builds: dict[str, dict] = {}
        self.deployments: dict[str, str] = {}

    @staticmethod
    def _validate_claim(row: dict) -> None:
        has_owner = row.get("claim_owner") is not None
        has_expiry = row.get("claim_expires_at") is not None
        if row["status"] == "building":
            if not has_owner or not has_expiry:
                raise _ConstraintViolation("building requires an atomic claim owner/expiry pair")
        elif has_owner or has_expiry:
            raise _ConstraintViolation("non-building state cannot retain a claim")

    def _validate_retry(self, row: dict) -> None:
        retry_id = row.get("retry_of_build_id")
        if retry_id is None:
            return
        prior = self.builds.get(retry_id)
        if prior is None or prior["revision_id"] != row["revision_id"]:
            raise _ConstraintViolation("retry build must belong to the same revision")

    def insert_build(self, row: dict) -> None:
        self._validate_claim(row)
        self._validate_retry(row)
        if row["id"] in self.builds:
            raise _ConstraintViolation("duplicate build id")
        self.builds[row["id"]] = dict(row)

    def update_build(self, build_id: str, **changes) -> None:
        current = self.builds[build_id]
        updated = {**current, **changes}
        for field in _BUILD_IDENTITY_FIELDS:
            if updated.get(field) != current.get(field):
                raise _ConstraintViolation(f"immutable build identity field: {field}")
        validate_revision_image_build_transition(current["status"], updated["status"])
        self._validate_claim(updated)
        self._validate_retry(updated)
        self.builds[build_id] = updated

    def insert_deployment(self, deployment_id: str, endpoint_id: str) -> None:
        self.deployments[deployment_id] = endpoint_id

    def insert_container(self, deployment_id: str, endpoint_id: str) -> None:
        if self.deployments.get(deployment_id) != endpoint_id:
            raise _ConstraintViolation("container deployment must belong to the same endpoint")


class _Transaction:
    def __init__(self, connection: "_EndpointWriteConnection") -> None:
        self.connection = connection
        self.snapshot = None

    async def __aenter__(self):
        self.connection.transaction_entries += 1
        self.snapshot = (deepcopy(self.connection.endpoints), deepcopy(self.connection.deployments))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.connection.endpoints, self.connection.deployments = self.snapshot
            self.connection.transaction_rollbacks += 1
        return False


class _EndpointWriteConnection(_SchemaConnection):
    def __init__(self) -> None:
        super().__init__()
        self.fail_legacy_write = False
        self.transaction_entries = 0
        self.transaction_rollbacks = 0

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        self.queries.append(normalized)
        if normalized.startswith("insert into bundle_endpoints"):
            row = {
                "id": params[0],
                "module_import_id": params[1],
                "lm_profile_id": params[2],
                "pinned_worker_count": params[3],
                "name": params[4],
                "key_preview": params[6],
                "created_at": params[7],
                "updated_at": params[8],
            }
            self.endpoints[row["id"]] = row
            return dict(row)
        if normalized.startswith("update bundle_endpoints"):
            row = self.endpoints.get(params[0])
            if row is None:
                return None
            if "where id = $1 and module_import_id = $2" in normalized:
                if row["module_import_id"] != params[1]:
                    return None
                row.update(
                    name=params[2],
                    lm_profile_id=params[3],
                    pinned_worker_count=params[4],
                    updated_at=params[5],
                )
            else:
                row.update(
                    name=params[1],
                    module_import_id=params[2],
                    lm_profile_id=params[3],
                    pinned_worker_count=params[4],
                    updated_at=params[5],
                )
            return dict(row)
        return await super().fetchrow(query, *params)

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if self.fail_legacy_write and (
            normalized.startswith("insert into endpoint_deployments")
            or normalized.startswith("update endpoint_deployments set desired_replica_count")
        ):
            raise RuntimeError("deployment intent write failed")
        return await super().execute(query, *params)


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


def _build_row(
    build_id: str,
    revision_id: str,
    generation: int,
    *,
    status: str = "queued",
    retry_of_build_id: str | None = None,
    claim_owner: str | None = None,
    claim_expires_at: datetime | None = None,
) -> dict:
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    return {
        "id": build_id,
        "revision_id": revision_id,
        "generation": generation,
        "source_commit": f"commit-{revision_id}",
        "source_snapshot_path": f"/snapshots/{revision_id}.tar",
        "source_content_digest": f"sha256:{revision_id}",
        "local_tag": f"dspy-trainer/{revision_id}:g{generation}",
        "image_id": None,
        "image_digest": None,
        "base_image_id": "sha256:base",
        "status": status,
        "attempt": 0,
        "available_at": now,
        "claim_owner": claim_owner,
        "claim_expires_at": claim_expires_at,
        "build_log": "",
        "failure_reason": None,
        "retry_of_build_id": retry_of_build_id,
        "queued_at": now,
        "started_at": None,
        "finished_at": None,
        "created_at": now,
        "updated_at": now,
    }


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

            schema = chr(10).join(connection.queries)
            assert "create table if not exists revision_image_builds" in schema
            assert "unique (revision_id, generation)" in schema
            assert "source_snapshot_path text not null" in schema
            assert "source_content_digest text not null" in schema
            assert "uq_revision_image_builds_active_revision" in schema
            assert "idx_revision_image_builds_fifo" in schema
            assert "idx_revision_image_builds_claim_recovery" in schema
            assert f"octet_length(build_log) <= {MAX_BUILD_LOG_BYTES}" in schema
            assert "if new.id is distinct from old.id" in schema
            assert "revision image build identity is immutable" in schema
            assert "foreign key (retry_of_build_id, revision_id)" in schema
            assert "status = 'building' and claim_owner is not null and claim_expires_at is not null" in schema
            assert "create table if not exists endpoint_deployments" in schema
            assert "unique (id, endpoint_id)" in schema
            assert "references revision_image_builds(id, revision_id) match full on delete restrict" in schema
            assert "create table if not exists managed_endpoint_containers" in schema
            assert "endpoint_id text not null references bundle_endpoints(id) on delete restrict" in schema
            assert "foreign key (deployment_id, endpoint_id)" in schema
            assert "references endpoint_deployments(id, endpoint_id) match full on delete restrict" in schema

    asyncio.run(scenario())


def test_build_identity_claim_retry_and_container_endpoint_constraints_are_enforced_by_faithful_fake():
    async def scenario():
        with _service_module() as module:
            connection = _SchemaConnection()
            await _services(module, connection).init_db()
            contract = _RevisionImageConstraintFake(chr(10).join(connection.queries))
            prior = _build_row("build-prior", "revision-a", 1, status="failed")
            contract.insert_build(prior)

            with pytest.raises(_ConstraintViolation, match="immutable build identity field: id"):
                contract.update_build("build-prior", id="build-rewritten")
            with pytest.raises(_ConstraintViolation, match="immutable build identity field: source_content_digest"):
                contract.update_build("build-prior", source_content_digest="sha256:rewritten")

            queued = _build_row("build-queued", "revision-a", 2, retry_of_build_id="build-prior")
            contract.insert_build(queued)
            with pytest.raises(_ConstraintViolation, match="atomic claim owner/expiry pair"):
                contract.update_build("build-queued", status="building")
            with pytest.raises(_ConstraintViolation, match="atomic claim owner/expiry pair"):
                contract.update_build("build-queued", status="building", claim_owner="deployer-1")
            with pytest.raises(_ConstraintViolation, match="non-building state cannot retain a claim"):
                contract.insert_build(
                    _build_row(
                        "build-invalid-claim",
                        "revision-b",
                        1,
                        claim_owner="deployer-1",
                        claim_expires_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
                    )
                )

            claim_expiry = datetime(2026, 9, 25, 1, tzinfo=timezone.utc)
            contract.update_build(
                "build-queued",
                status="building",
                claim_owner="deployer-1",
                claim_expires_at=claim_expiry,
            )
            assert contract.builds["build-queued"]["claim_expires_at"] == claim_expiry

            with pytest.raises(_ConstraintViolation, match="same revision"):
                contract.insert_build(
                    _build_row(
                        "build-cross-revision-retry",
                        "revision-b",
                        1,
                        retry_of_build_id="build-prior",
                    )
                )

            contract.insert_deployment("deployment-a", "endpoint-a")
            contract.insert_container("deployment-a", "endpoint-a")
            with pytest.raises(_ConstraintViolation, match="same endpoint"):
                contract.insert_container("deployment-a", "endpoint-b")

    asyncio.run(scenario())


def test_endpoint_and_legacy_deployment_writes_roll_back_together():
    async def scenario():
        with _service_module() as module:
            connection = _EndpointWriteConnection()
            services = _services(module, connection)

            async def get_module(module_id):
                return {"id": module_id}

            async def get_endpoint(endpoint_id):
                row = connection.endpoints.get(endpoint_id)
                return dict(row) if row is not None else None

            async def reconcile():
                return None

            services.get_module = get_module
            services.get_bundle_endpoint = get_endpoint
            services.reconcile_endpoint_worker_assignments = reconcile
            connection.fail_legacy_write = True
            endpoints_before_create = deepcopy(connection.endpoints)

            with pytest.raises(RuntimeError, match="deployment intent write failed"):
                await services.create_bundle_endpoint("module-a", "new endpoint", pinned_worker_count=2)

            assert connection.endpoints == endpoints_before_create
            existing = connection.endpoints["endpoint-existing"]
            existing.update(
                module_import_id="module-a",
                lm_profile_id=None,
                name="before",
                key_preview="preview",
            )
            endpoint_before_update = deepcopy(existing)

            with pytest.raises(RuntimeError, match="deployment intent write failed"):
                await services.update_bundle_endpoint(
                    "module-a",
                    "endpoint-existing",
                    "after",
                    pinned_worker_count=7,
                )

            assert connection.endpoints["endpoint-existing"] == endpoint_before_update
            assert connection.transaction_entries == 2
            assert connection.transaction_rollbacks == 2

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
            await services._update_endpoint_deployment_replica_count(
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


def test_state_transition_helpers_reject_invalid_state_jumps():
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
