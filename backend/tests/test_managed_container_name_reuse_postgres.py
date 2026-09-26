from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.endpoint_container_reconciler import (
    ContainerIdentity,
    ObservedContainer,
    PostgresEndpointContainerStore,
)
from app.services import _migrate_managed_container_name_uniqueness

NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)
CONTAINER_NAME = "dspy-endpoint-slot-0"


def _identity(*, worker_id: str) -> ContainerIdentity:
    return ContainerIdentity(
        owner="deployment-owner",
        compose_project="compose-project",
        endpoint_id="endpoint-1",
        deployment_id="deployment-1",
        slot=0,
        build_id="build-1",
        revision_id="revision-1",
        rollout_generation=1,
        worker_id=worker_id,
        image_id="sha256:image-1",
    )


def _container(container_id: str) -> ObservedContainer:
    return ObservedContainer(
        container_id=container_id,
        name=CONTAINER_NAME,
        image_id="sha256:image-1",
        labels={},
        state="running",
        created_at=NOW,
    )


def _test_dsn() -> str:
    dsn = os.environ.get("DSPY_TRAINER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "set DSPY_TRAINER_TEST_POSTGRES_DSN to run PostgreSQL store regressions"
        )
    return dsn


async def _create_legacy_schema(connection, schema: str) -> None:
    await connection.execute(f'create schema "{schema}"')
    await connection.execute(f'set search_path to "{schema}"')
    await connection.execute("""
        create table managed_endpoint_containers (
          container_id text primary key,
          container_name text not null unique,
          endpoint_id text not null,
          deployment_id text not null,
          build_id text not null,
          revision_id text not null,
          slot int not null,
          worker_id text,
          lifecycle text not null,
          last_observed_at timestamptz,
          started_at timestamptz,
          stopped_at timestamptz,
          drain_started_at timestamptz,
          drain_timed_out boolean not null default false,
          failure_reason text,
          container_log text not null default '',
          created_at timestamptz not null,
          updated_at timestamptz not null
        );
        create index unrelated_managed_endpoint_id_idx
        on managed_endpoint_containers(endpoint_id);
        """)


@asynccontextmanager
async def _postgres_store():
    dsn = _test_dsn()
    schema = f"managed_name_{uuid.uuid4().hex}"
    connection = await asyncpg.connect(dsn)
    try:
        await _create_legacy_schema(connection, schema)
        await _migrate_managed_container_name_uniqueness(connection)
        await _migrate_managed_container_name_uniqueness(connection)

        store = PostgresEndpointContainerStore(
            postgres_dsn=dsn,
            instance_id="test",
            leader_timeout_seconds=15,
        )
        store._connection = connection
        yield connection, store
    finally:
        await connection.execute("set search_path to public")
        await connection.execute(f'drop schema if exists "{schema}" cascade')
        await connection.close()


@pytest.mark.asyncio
async def test_store_reuses_removed_name_and_preserves_history():
    async with _postgres_store() as (connection, store):
        constraint_count = await connection.fetchval("""
            select count(*)
            from pg_constraint
            where conrelid = 'managed_endpoint_containers'::regclass
              and contype = 'u'
            """)
        index = await connection.fetchrow("""
            select idx.indisunique,
                   pg_get_expr(idx.indpred, idx.indrelid) as predicate
            from pg_index idx
            join pg_class cls on cls.oid = idx.indexrelid
            where cls.relname = 'uq_managed_endpoint_containers_active_name'
            """)
        assert constraint_count == 0
        assert index is not None
        assert index["indisunique"] is True
        assert "lifecycle" in index["predicate"]
        assert "removed" in index["predicate"]

        await store.observe_container(
            _identity(worker_id="worker-old"),
            _container("container-old"),
            now=NOW,
        )
        await store.record_container_removed(
            "container-old",
            now=NOW,
            timed_out=False,
            reason="replacement requested",
            logs="old logs",
        )
        await store.observe_container(
            _identity(worker_id="worker-new"),
            _container("container-new"),
            now=NOW,
        )

        rows = await connection.fetch("""
            select container_id, container_name, lifecycle
            from managed_endpoint_containers
            order by container_id
            """)
        assert [dict(row) for row in rows] == [
            {
                "container_id": "container-new",
                "container_name": CONTAINER_NAME,
                "lifecycle": "ready",
            },
            {
                "container_id": "container-old",
                "container_name": CONTAINER_NAME,
                "lifecycle": "removed",
            },
        ]
        assert (
            await connection.fetchval(
                """
                select count(*)
                from managed_endpoint_containers
                where container_name = $1 and lifecycle <> 'removed'
                """,
                CONTAINER_NAME,
            )
            == 1
        )


@pytest.mark.asyncio
async def test_store_rejects_simultaneous_active_name_collision():
    async with _postgres_store() as (connection, store):
        await store.observe_container(
            _identity(worker_id="worker-first"),
            _container("container-first"),
            now=NOW,
        )

        with pytest.raises(asyncpg.UniqueViolationError):
            await store.observe_container(
                _identity(worker_id="worker-second"),
                _container("container-second"),
                now=NOW,
            )

        assert (
            await connection.fetchval(
                """
                select count(*)
                from managed_endpoint_containers
                where container_name = $1 and lifecycle <> 'removed'
                """,
                CONTAINER_NAME,
            )
            == 1
        )


class _PausedMigrationConnection:
    def __init__(
        self, connection, lock_acquired: asyncio.Event, release: asyncio.Event
    ):
        self._connection = connection
        self._lock_acquired = lock_acquired
        self._release = release

    def transaction(self):
        return self._connection.transaction()

    async def execute(self, query: str, *args):
        result = await self._connection.execute(query, *args)
        if "pg_advisory_xact_lock" in query:
            self._lock_acquired.set()
            await self._release.wait()
        return result


@pytest.mark.asyncio
async def test_simultaneous_migrations_serialize_and_preserve_unrelated_index():
    dsn = _test_dsn()
    schema = f"managed_name_concurrent_{uuid.uuid4().hex}"
    first = await asyncpg.connect(dsn)
    second = await asyncpg.connect(dsn)
    lock_acquired = asyncio.Event()
    release = asyncio.Event()
    first_task = None
    second_task = None

    try:
        await _create_legacy_schema(first, schema)
        await second.execute(f'set search_path to "{schema}"')
        paused_first = _PausedMigrationConnection(first, lock_acquired, release)

        first_task = asyncio.create_task(
            _migrate_managed_container_name_uniqueness(paused_first)
        )
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        second_task = asyncio.create_task(
            _migrate_managed_container_name_uniqueness(second)
        )
        await asyncio.sleep(0.1)
        assert not second_task.done(), "second migration did not wait for serialization"

        release.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)
        first_task = None
        second_task = None

        indexes = await first.fetch("""
            select cls.relname,
                   idx.indisunique,
                   pg_get_expr(idx.indpred, idx.indrelid) as predicate
            from pg_index idx
            join pg_class cls on cls.oid = idx.indexrelid
            where idx.indrelid = 'managed_endpoint_containers'::regclass
            order by cls.relname
            """)
        intended = [
            row
            for row in indexes
            if row["relname"] == "uq_managed_endpoint_containers_active_name"
        ]
        assert len(intended) == 1
        assert intended[0]["indisunique"] is True
        assert "lifecycle" in intended[0]["predicate"]
        assert "removed" in intended[0]["predicate"]
        assert all(
            row["relname"] != "managed_endpoint_containers_container_name_key"
            for row in indexes
        )
        assert any(
            row["relname"] == "unrelated_managed_endpoint_id_idx" for row in indexes
        )
        assert await first.fetchval("""
                select count(*)
                from pg_constraint
                where conrelid = 'managed_endpoint_containers'::regclass
                  and conname = 'managed_endpoint_containers_container_name_key'
                """) == 0
    finally:
        release.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await second.close()
        await first.execute("set search_path to public")
        await first.execute(f'drop schema if exists "{schema}" cascade')
        await first.close()
