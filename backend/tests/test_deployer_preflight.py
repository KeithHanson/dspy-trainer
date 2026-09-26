from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DeployerSettings
from app.deployer_preflight import (
    check_liveness,
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
    "deployer_runtime_state",
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


class FakeConnection:
    def __init__(self, *, state=None, active_builds: int = 0) -> None:
        self.state = state
        self.active_builds = active_builds
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    async def fetch(self, query: str, *_args):
        assert "information_schema.tables" in query
        return [{"table_name": name} for name in REQUIRED_TABLES]

    async def fetchrow(self, query: str, *_args):
        assert "deployer_runtime_state" in query
        return self.state

    async def fetchval(self, query: str, *_args):
        assert "revision_image_builds" in query
        return self.active_builds

    async def execute(self, query: str, *args):
        self.executions.append((query, args))
        return "INSERT 0 1"

    async def close(self) -> None:
        self.closed = True


def connector(connection: FakeConnection):
    async def connect(_dsn: str):
        return connection

    return connect


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
    assert len(connection.executions) == 1
    assert connection.executions[0][1] == (
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
        "leader_instance_id": None,
        "leader_heartbeat_at": None,
        "build_leader": False,
        "endpoint_leader": False,
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
async def test_readiness_requires_fresh_complete_leader_heartbeat():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    state = {
        "base_image_name": "dspy-trainer-backend:local",
        "base_image_id": IMAGE_ID,
        "leader_instance_id": "controller-a",
        "leader_heartbeat_at": now - timedelta(seconds=2),
        "build_leader": True,
        "endpoint_leader": True,
    }

    report = await run_deployer_preflight(
        settings(),
        docker_client=FakeDocker(),
        connect=connector(FakeConnection(state=state)),
        require_leader_heartbeat=True,
        now=now,
    )

    assert report.ok
    heartbeat = next(
        check for check in report.checks if check.name == "leader_heartbeat"
    )
    assert heartbeat.ok


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
        "leader_instance_id": "old-controller",
        "leader_heartbeat_at": datetime.now(timezone.utc),
        "build_leader": True,
        "endpoint_leader": True,
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
    assert connection.executions[0][1][-1] == IMAGE_ID


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
