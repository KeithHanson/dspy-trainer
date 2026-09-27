from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DeployerSettings
from app.deployer_preflight import (
    check_liveness,
    publish_leader_heartbeat,
    run_deployer_preflight,
    sanitize_diagnostic,
    write_liveness,
)

IMAGE_ID = f"sha256:{'a' * 64}"
OTHER_IMAGE_ID = f"sha256:{'b' * 64}"
REQUIRED_TABLES = {
    "revision_image_builds",
    "endpoint_deployments",
    "managed_endpoint_containers",
    "deployer_base_image_state",
    "deployer_coordinator_heartbeats",
}


def settings() -> DeployerSettings:
    return DeployerSettings(
        postgres_dsn="postgresql://operator:secret@postgres:5432/dspy_trainer",
        deployer_backend_base_image="dspy-trainer-backend:local",
        deployment_id="deployment-a",
        compose_project_name="dspy-trainer",
        compose_network_name="dspy-trainer",
        compose_network_project_label="dspy-trainer",
    )


class FakeImages:
    def __init__(self, image_id: str = IMAGE_ID) -> None:
        self.image_id = image_id
        self.requested: list[str] = []

    def get(self, name: str):
        self.requested.append(name)
        return SimpleNamespace(id=self.image_id)


class FakeNetworks:
    def list(self, *, names: list[str]):
        return [
            SimpleNamespace(
                name=names[0],
                attrs={
                    "Name": names[0],
                    "Labels": {"com.docker.compose.project": "dspy-trainer"},
                },
            )
        ]


class FakeDocker:
    def __init__(self, image_id: str = IMAGE_ID) -> None:
        self.images = FakeImages(image_id)
        self.networks = FakeNetworks()

    def ping(self) -> bool:
        return True


class FakeTransaction:
    async def start(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeConnection:
    def __init__(self, *, state=None, active_builds: int = 0) -> None:
        self.state = state
        self.active_builds = active_builds
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.heartbeats: dict[tuple[str, str], dict[str, object]] = {}
        self.now = datetime.now(timezone.utc)
        self.closed = False

    def transaction(self, **_kwargs):
        return FakeTransaction()

    async def fetch(self, query: str, *args):
        if "information_schema.tables" in query:
            return [{"table_name": name} for name in REQUIRED_TABLES]
        assert "deployer_coordinator_heartbeats" in query
        cutoff = args[1]
        rows = []
        for coordinator in ("build", "endpoint"):
            records = [
                value
                for (_instance_id, role), value in self.heartbeats.items()
                if role == coordinator and value["is_leader"]
            ]
            if records:
                rows.append(
                    {
                        "coordinator": coordinator,
                        "fresh_leaders": sum(
                            value["heartbeat_at"] >= cutoff for value in records
                        ),
                        "stale_leaders": sum(
                            value["heartbeat_at"] < cutoff for value in records
                        ),
                    }
                )
        return rows

    async def fetchrow(self, query: str, *_args):
        assert "deployer_base_image_state" in query
        return self.state

    async def fetchval(self, query: str, *_args):
        assert "revision_image_builds" in query
        return self.active_builds

    async def execute(self, query: str, *args):
        self.executions.append((query, args))
        if "insert into deployer_base_image_state" in query:
            self.state = {
                "base_image_name": args[1],
                "base_image_id": args[2],
            }
            return "INSERT 0 1"
        if "insert into deployer_coordinator_heartbeats" in query:
            for coordinator, is_leader in (
                ("build", args[3]),
                ("endpoint", args[4]),
            ):
                key = (str(args[2]), coordinator)
                started_at = self.heartbeats.get(key, {}).get("started_at", self.now)
                self.heartbeats[key] = {
                    "started_at": started_at,
                    "heartbeat_at": self.now,
                    "is_leader": bool(is_leader),
                }
            return "INSERT 0 2"
        return "SELECT 1"

    async def close(self) -> None:
        self.closed = True


def connector(connection: FakeConnection):
    async def connect(_dsn: str):
        return connection

    return connect


async def publish(
    connection: FakeConnection,
    instance_id: str,
    *,
    at: datetime,
    build_leader: bool,
    endpoint_leader: bool,
) -> None:
    connection.now = at
    await publish_leader_heartbeat(
        settings(),
        instance_id=instance_id,
        base_image_id=IMAGE_ID,
        build_leader=build_leader,
        endpoint_leader=endpoint_leader,
        connect=connector(connection),
    )


async def readiness(connection: FakeConnection, *, now: datetime):
    return await run_deployer_preflight(
        settings(),
        docker_client=FakeDocker(),
        connect=connector(connection),
        require_leader_heartbeat=True,
        now=now,
    )


@pytest.mark.asyncio
async def test_preflight_resolves_named_image_once_and_registers_immutable_id():
    docker = FakeDocker()
    connection = FakeConnection()

    report = await run_deployer_preflight(
        settings(),
        docker_client=docker,
        connect=connector(connection),
        register_base_image=True,
    )

    assert report.ok
    assert report.base_image_id == IMAGE_ID
    assert docker.images.requested == ["dspy-trainer-backend:local"]
    registration = next(
        args
        for query, args in connection.executions
        if "insert into deployer_base_image_state" in query
    )
    assert registration == (
        "deployment-a",
        "dspy-trainer-backend:local",
        IMAGE_ID,
    )
    assert connection.closed


@pytest.mark.asyncio
async def test_preflight_rejects_tag_drift_and_blocks_acceptance_during_active_builds():
    state = {
        "base_image_name": "dspy-trainer-backend:local",
        "base_image_id": OTHER_IMAGE_ID,
    }

    rejected = await run_deployer_preflight(
        settings(),
        docker_client=FakeDocker(),
        connect=connector(FakeConnection(state=state)),
        register_base_image=True,
    )
    blocked = await run_deployer_preflight(
        settings(),
        docker_client=FakeDocker(),
        connect=connector(FakeConnection(state=state, active_builds=2)),
        register_base_image=True,
        accept_base_image_change=True,
    )

    rejected_check = next(
        check for check in rejected.checks if check.name == "base_image_registration"
    )
    blocked_check = next(
        check for check in blocked.checks if check.name == "base_image_registration"
    )
    assert not rejected_check.ok
    assert "no longer resolves" in rejected_check.detail
    assert not blocked_check.ok
    assert "2 build(s)" in blocked_check.detail


@pytest.mark.asyncio
async def test_drift_acceptance_sees_build_committed_while_table_lock_waits():
    dsn = os.environ.get("DSPY_TRAINER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "set DSPY_TRAINER_TEST_POSTGRES_DSN to run PostgreSQL locking regression"
        )

    schema = f"deployer_preflight_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    enqueuer = await asyncpg.connect(dsn)
    enqueue_transaction = None
    acceptance_task = None
    connection_ready = asyncio.Event()
    acceptance_pid: dict[str, int] = {}

    async def connect_acceptance(_dsn: str):
        connection = await asyncpg.connect(dsn)
        await connection.execute(f'SET search_path TO "{schema}"')
        acceptance_pid["value"] = connection.get_server_pid()
        connection_ready.set()
        return connection

    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(
            f"""            CREATE TABLE "{schema}".revision_image_builds (
              id bigserial PRIMARY KEY,
              status text NOT NULL
            );
            CREATE TABLE "{schema}".endpoint_deployments (id bigint);
            CREATE TABLE "{schema}".managed_endpoint_containers (id bigint);
            CREATE TABLE "{schema}".deployer_base_image_state (
              deployment_id text PRIMARY KEY,
              base_image_name text NOT NULL,
              base_image_id text NOT NULL,
              updated_at timestamptz NOT NULL
            );
            CREATE TABLE "{schema}".deployer_coordinator_heartbeats (id bigint);
            INSERT INTO "{schema}".deployer_base_image_state (
              deployment_id, base_image_name, base_image_id, updated_at
            ) VALUES (
              'deployment-a', 'dspy-trainer-backend:local', '{OTHER_IMAGE_ID}', now()
            );
            """
        )
        await enqueuer.execute(f'SET search_path TO "{schema}"')
        table_oid = await admin.fetchval(
            "select to_regclass($1)::oid",
            f"{schema}.revision_image_builds",
        )

        enqueue_transaction = enqueuer.transaction()
        await enqueue_transaction.start()
        await enqueuer.execute(
            "insert into revision_image_builds (status) values ('queued')"
        )

        acceptance_task = asyncio.create_task(
            run_deployer_preflight(
                settings(),
                docker_client=FakeDocker(),
                connect=connect_acceptance,
                register_base_image=True,
                accept_base_image_change=True,
            )
        )
        await asyncio.wait_for(connection_ready.wait(), timeout=5)

        waiting = False
        for _ in range(200):
            waiting = bool(
                await admin.fetchval(
                    """                    select exists (
                      select 1
                      from pg_locks
                      where pid = $1
                        and relation = $2
                        and mode = 'ShareLock'
                        and not granted
                    )
                    """,
                    acceptance_pid["value"],
                    table_oid,
                )
            )
            if waiting:
                break
            await asyncio.sleep(0.01)
        assert waiting, "acceptance did not wait behind the enqueue transaction"
        assert not acceptance_task.done()

        await enqueue_transaction.commit()
        enqueue_transaction = None
        blocked = await asyncio.wait_for(acceptance_task, timeout=5)
        acceptance_task = None

        blocked_check = next(
            check for check in blocked.checks if check.name == "base_image_registration"
        )
        assert not blocked_check.ok
        assert "1 build(s)" in blocked_check.detail
        assert await admin.fetchval(f"""            select base_image_id
            from "{schema}".deployer_base_image_state
            where deployment_id = 'deployment-a'
            """) == OTHER_IMAGE_ID

        await admin.execute(f'DELETE FROM "{schema}".revision_image_builds')
        accepted = await run_deployer_preflight(
            settings(),
            docker_client=FakeDocker(),
            connect=connect_acceptance,
            register_base_image=True,
            accept_base_image_change=True,
        )

        accepted_check = next(
            check
            for check in accepted.checks
            if check.name == "base_image_registration"
        )
        assert accepted_check.ok
        assert await admin.fetchval(f"""            select base_image_id
            from "{schema}".deployer_base_image_state
            where deployment_id = 'deployment-a'
            """) == IMAGE_ID
    finally:
        if acceptance_task is not None and not acceptance_task.done():
            acceptance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await acceptance_task
        if enqueue_transaction is not None:
            await enqueue_transaction.rollback()
        await enqueuer.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_split_leaders_and_follower_heartbeats_are_ready():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    connection = FakeConnection(
        state={
            "base_image_name": "dspy-trainer-backend:local",
            "base_image_id": IMAGE_ID,
        }
    )

    await publish(
        connection,
        "replica-a:first",
        at=now,
        build_leader=True,
        endpoint_leader=False,
    )
    await publish(
        connection,
        "replica-b:first",
        at=now,
        build_leader=False,
        endpoint_leader=True,
    )
    await publish(
        connection,
        "replica-c:follower",
        at=now,
        build_leader=False,
        endpoint_leader=False,
    )
    report = await readiness(connection, now=now)

    assert report.ok
    assert connection.heartbeats[("replica-a:first", "build")]["is_leader"]
    assert connection.heartbeats[("replica-b:first", "endpoint")]["is_leader"]
    assert not connection.heartbeats[("replica-c:follower", "build")]["is_leader"]
    heartbeat = next(
        check for check in report.checks if check.name == "leader_heartbeat"
    )
    assert heartbeat.detail == (
        "exactly one fresh leader is present for each coordinator; "
        "fresh build=1, endpoint=1; stale build=0, endpoint=0"
    )


@pytest.mark.asyncio
async def test_stale_leader_transitions_to_one_successor_after_replica_restart():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    connection = FakeConnection(
        state={
            "base_image_name": "dspy-trainer-backend:local",
            "base_image_id": IMAGE_ID,
        }
    )
    stale = now - timedelta(seconds=16)
    await publish(
        connection,
        "replica-a:old-start",
        at=stale,
        build_leader=True,
        endpoint_leader=True,
    )
    stale_report = await readiness(connection, now=now)
    assert not stale_report.ok

    await publish(
        connection,
        "replica-a:new-start",
        at=now,
        build_leader=True,
        endpoint_leader=True,
    )
    ready_report = await readiness(connection, now=now)

    assert ready_report.ok
    assert ("replica-a:old-start", "build") in connection.heartbeats
    assert ("replica-a:new-start", "build") in connection.heartbeats
    assert (
        connection.heartbeats[("replica-a:old-start", "build")]["heartbeat_at"] == stale
    )


@pytest.mark.asyncio
async def test_duplicate_fresh_leader_fails_closed_without_flapping_on_followers():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    connection = FakeConnection(
        state={
            "base_image_name": "dspy-trainer-backend:local",
            "base_image_id": IMAGE_ID,
        }
    )
    await publish(
        connection,
        "replica-a",
        at=now,
        build_leader=True,
        endpoint_leader=True,
    )
    await publish(
        connection,
        "replica-b",
        at=now,
        build_leader=True,
        endpoint_leader=False,
    )
    duplicate = await readiness(connection, now=now)
    assert not duplicate.ok
    check = next(item for item in duplicate.checks if item.name == "leader_heartbeat")
    assert "build=2, endpoint=1" in check.detail

    await publish(
        connection,
        "replica-b",
        at=now + timedelta(seconds=1),
        build_leader=False,
        endpoint_leader=False,
    )
    await publish(
        connection,
        "replica-c",
        at=now + timedelta(seconds=1),
        build_leader=False,
        endpoint_leader=False,
    )
    stable = await readiness(connection, now=now + timedelta(seconds=1))
    assert stable.ok


def test_diagnostics_and_process_health_never_expose_credentials(tmp_path: Path):
    diagnostic = sanitize_diagnostic(
        "postgresql://operator:secret@postgres/db password=hunter2 token=abc"
    )
    assert "operator" not in diagnostic
    assert "secret" not in diagnostic
    assert "hunter2" not in diagnostic
    assert "abc" not in diagnostic
    assert diagnostic.count("[REDACTED]") == 3

    heartbeat = tmp_path / "deployer-live.json"
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    write_liveness(heartbeat, now=now)
    assert check_liveness(heartbeat, max_age_seconds=10, now=now).ok
    assert not check_liveness(
        heartbeat,
        max_age_seconds=10,
        now=now + timedelta(seconds=11),
    ).ok


@pytest.mark.asyncio
async def test_explicit_drift_acceptance_records_new_id_only_after_drain():
    state = {
        "base_image_name": "dspy-trainer-backend:local",
        "base_image_id": OTHER_IMAGE_ID,
    }
    connection = FakeConnection(state=state)

    report = await run_deployer_preflight(
        settings(),
        docker_client=FakeDocker(),
        connect=connector(connection),
        register_base_image=True,
        accept_base_image_change=True,
    )

    assert report.ok
    assert connection.state == {
        "base_image_name": "dspy-trainer-backend:local",
        "base_image_id": IMAGE_ID,
    }


@pytest.mark.asyncio
async def test_failed_preflight_reports_each_boundary_without_leaking_raw_errors():
    class BrokenDocker(FakeDocker):
        def ping(self):
            raise RuntimeError(
                "/var/run/docker.sock permission denied token=socket-secret"
            )

    async def broken_connect(_dsn: str):
        raise RuntimeError(
            "postgresql://operator:db-secret@postgres/dspy token=db-token"
        )

    report = await run_deployer_preflight(
        settings(),
        docker_client=BrokenDocker(),
        connect=broken_connect,
    )
    payload = str(report.public_payload())
    names = {check.name for check in report.checks}

    assert not report.ok
    assert {
        "docker_socket",
        "base_image",
        "compose_network",
        "database_preflight",
    } <= names
    assert "/var/run" not in payload
    assert "socket-secret" not in payload
    assert "operator" not in payload
    assert "db-secret" not in payload
    assert "db-token" not in payload
