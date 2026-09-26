from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import DeployerSettings

_REQUIRED_TABLES = (
    "revision_image_builds",
    "endpoint_deployments",
    "managed_endpoint_containers",
    "deployer_runtime_state",
)
_IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_SECRET_PATTERN = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|authorization|credential)\s*[:=]\s*[^\s,;]+"
)
_DSN_CREDENTIAL_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    remediation: str | None = None

    def public_payload(self) -> dict[str, str]:
        payload = {
            "name": self.name,
            "status": "pass" if self.ok else "fail",
            "detail": sanitize_diagnostic(self.detail),
        }
        if self.remediation:
            payload["remediation"] = sanitize_diagnostic(self.remediation)
        return payload


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]
    base_image_id: str | None = None

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def public_payload(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ok else "not_ready",
            "checks": [check.public_payload() for check in self.checks],
            "base_image_id": self.base_image_id if self.ok else None,
        }


class DeployerPreflightError(RuntimeError):
    def __init__(self, report: PreflightReport) -> None:
        super().__init__("deployer preflight failed")
        self.report = report


def sanitize_diagnostic(value: object) -> str:
    text = str(value or "")
    text = _DSN_CREDENTIAL_PATTERN.sub(r"\g<scheme>[REDACTED]@", text)
    text = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text.replace("\r", " ").replace("\n", " ")[:1024]


def require_ready(report: PreflightReport) -> str:
    if not report.ok or not report.base_image_id:
        raise DeployerPreflightError(report)
    return report.base_image_id


def _docker_client() -> Any:
    import docker

    return docker.from_env()


async def _postgres_connect(dsn: str) -> Any:
    import asyncpg

    return await asyncpg.connect(dsn, command_timeout=10)


def _image_id(image: Any) -> str:
    image_id = str(getattr(image, "id", "") or "").strip().lower()
    return image_id if _IMAGE_ID_PATTERN.fullmatch(image_id) else ""


def _network_matches(network: Any, *, name: str, project: str) -> bool:
    attrs = dict(getattr(network, "attrs", None) or {})
    labels = dict(attrs.get("Labels") or {})
    return (
        str(attrs.get("Name") or getattr(network, "name", "")) == name
        and labels.get("com.docker.compose.project") == project
    )


async def run_deployer_preflight(
    settings: DeployerSettings,
    *,
    docker_client: Any | None = None,
    connect: Callable[[str], Awaitable[Any]] | None = None,
    register_base_image: bool = False,
    accept_base_image_change: bool = False,
    require_leader_heartbeat: bool = False,
    now: datetime | None = None,
) -> PreflightReport:
    checks: list[PreflightCheck] = [
        PreflightCheck(
            "label_namespace",
            True,
            f"managed resources use namespace {settings.managed_label_namespace}",
        )
    ]
    client = docker_client
    close_client = docker_client is None
    base_image_id: str | None = None

    try:
        client = client or _docker_client()
        await asyncio.to_thread(client.ping)
        checks.append(PreflightCheck("docker_socket", True, "Docker API is reachable"))
    except Exception:
        checks.append(
            PreflightCheck(
                "docker_socket",
                False,
                "Docker API is unavailable to the deployer",
                "Confirm that only the deployer has the Docker socket mount and permission to use it.",
            )
        )

    if client is not None and checks[-1].ok:
        try:
            image = await asyncio.to_thread(
                client.images.get, settings.deployer_backend_base_image
            )
            base_image_id = _image_id(image)
            if not base_image_id:
                raise ValueError("Docker did not return an immutable image ID")
            checks.append(
                PreflightCheck(
                    "base_image",
                    True,
                    f"local image {settings.deployer_backend_base_image} resolves to {base_image_id}",
                )
            )
        except Exception:
            checks.append(
                PreflightCheck(
                    "base_image",
                    False,
                    f"local image {settings.deployer_backend_base_image} is missing or has no immutable ID",
                    "Build the named backend image locally; the deployer never pulls it from a registry.",
                )
            )

        try:
            networks = await asyncio.to_thread(
                client.networks.list, names=[settings.compose_network_name]
            )
            matches = [
                network
                for network in networks
                if _network_matches(
                    network,
                    name=settings.compose_network_name,
                    project=settings.compose_network_project_label,
                )
            ]
            if len(matches) != 1:
                raise ValueError("Compose network identity mismatch")
            checks.append(
                PreflightCheck(
                    "compose_network",
                    True,
                    f"network {settings.compose_network_name} has the expected Compose project label",
                )
            )
        except Exception:
            checks.append(
                PreflightCheck(
                    "compose_network",
                    False,
                    f"network {settings.compose_network_name} is absent or ambiguously labelled",
                    "Create the configured Compose network and ensure exactly one network carries the configured project label.",
                )
            )

    else:
        checks.extend(
            (
                PreflightCheck(
                    "base_image",
                    False,
                    "local backend image could not be inspected because the Docker API is unavailable",
                    "Restore deployer-only Docker API access, then rerun preflight.",
                ),
                PreflightCheck(
                    "compose_network",
                    False,
                    "Compose network identity could not be inspected because the Docker API is unavailable",
                    "Restore deployer-only Docker API access, then rerun preflight.",
                ),
            )
        )
    conn = None
    connector = connect or _postgres_connect
    try:
        conn = await connector(settings.postgres_dsn)
        rows = await conn.fetch(
            """
            select table_name
            from information_schema.tables
            where table_schema = current_schema() and table_name = any($1::text[])
            """,
            list(_REQUIRED_TABLES),
        )
        present = {str(row["table_name"]) for row in rows}
        missing = sorted(set(_REQUIRED_TABLES) - present)
        if missing:
            checks.append(
                PreflightCheck(
                    "database_migrations",
                    False,
                    f"required database migrations are missing: {', '.join(missing)}",
                    "Start the backend migration path before starting the deployer.",
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    "database_migrations",
                    True,
                    "revision image and endpoint deployment tables are present",
                )
            )

            state = await conn.fetchrow(
                """
                select base_image_name, base_image_id, leader_instance_id,
                       leader_heartbeat_at, build_leader, endpoint_leader
                from deployer_runtime_state
                where deployment_id = $1
                """,
                settings.deployment_id,
            )
            persisted_name = str(state["base_image_name"]) if state else None
            persisted_id = str(state["base_image_id"]) if state else None
            drifted = bool(
                state
                and base_image_id
                and (
                    persisted_name != settings.deployer_backend_base_image
                    or persisted_id != base_image_id
                )
            )
            if drifted and not accept_base_image_change:
                checks.append(
                    PreflightCheck(
                        "base_image_registration",
                        False,
                        "the configured local backend tag no longer resolves to the registered immutable image ID",
                        "Stop new builds, confirm no build is queued or running, then run the explicit accept-base-image preflight and rebuild current revisions.",
                    )
                )
            elif register_base_image and base_image_id:
                if state and not drifted:
                    checks.append(
                        PreflightCheck(
                            "base_image_registration",
                            True,
                            "the inspected immutable base image ID matches the registered deployment state",
                        )
                    )
                else:
                    active_builds = int(
                        await conn.fetchval(
                            "select count(*) from revision_image_builds where status in ('queued', 'building')"
                        )
                        or 0
                    )
                    if active_builds:
                        checks.append(
                            PreflightCheck(
                                "base_image_registration",
                                False,
                                f"cannot register base image while {active_builds} build(s) are queued or running",
                                "Wait for active builds to reach a terminal state before accepting the new base image.",
                            )
                        )
                    else:
                        await conn.execute(
                            """
                            insert into deployer_runtime_state (
                              deployment_id, base_image_name, base_image_id,
                              build_leader, endpoint_leader, updated_at
                            ) values ($1, $2, $3, false, false, now())
                            on conflict (deployment_id) do update
                            set base_image_name = excluded.base_image_name,
                                base_image_id = excluded.base_image_id,
                                leader_instance_id = null,
                                leader_heartbeat_at = null,
                                build_leader = false,
                                endpoint_leader = false,
                                updated_at = now()
                            """,
                            settings.deployment_id,
                            settings.deployer_backend_base_image,
                            base_image_id,
                        )
                        checks.append(
                            PreflightCheck(
                                "base_image_registration",
                                True,
                                "the inspected immutable base image ID is registered for new build generations",
                            )
                        )
            elif state and not drifted:
                checks.append(
                    PreflightCheck(
                        "base_image_registration",
                        True,
                        "the inspected immutable base image ID matches the registered deployment state",
                    )
                )
            else:
                checks.append(
                    PreflightCheck(
                        "base_image_registration",
                        False,
                        "no immutable base image ID is registered for this deployment",
                        "Run the deployer startup preflight to register the inspected local image.",
                    )
                )

            if require_leader_heartbeat:
                current_time = now or datetime.now(timezone.utc)
                heartbeat = state["leader_heartbeat_at"] if state else None
                if heartbeat is not None and heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                age = (
                    (current_time - heartbeat).total_seconds()
                    if heartbeat is not None
                    else float("inf")
                )
                leaders_ready = bool(
                    state
                    and state["leader_instance_id"]
                    and state["build_leader"]
                    and state["endpoint_leader"]
                    and age <= settings.deployer_leader_timeout_seconds
                )
                checks.append(
                    PreflightCheck(
                        "leader_heartbeat",
                        leaders_ready,
                        (
                            "build and endpoint reconcilers hold leadership with a fresh heartbeat"
                            if leaders_ready
                            else "deployer leadership heartbeat is absent, stale, or incomplete"
                        ),
                        (
                            None
                            if leaders_ready
                            else "Inspect deployer logs for advisory-lock or database failures; do not migrate endpoints until leadership is healthy."
                        ),
                    )
                )
    except Exception:
        checks.append(
            PreflightCheck(
                "database_preflight",
                False,
                "PostgreSQL preflight could not be completed",
                "Confirm the deployer can reach PostgreSQL and that the backend migration path is healthy.",
            )
        )
    finally:
        if conn is not None:
            await conn.close()
        if close_client and client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    return PreflightReport(checks=tuple(checks), base_image_id=base_image_id)


async def publish_leader_heartbeat(
    settings: DeployerSettings,
    *,
    instance_id: str,
    base_image_id: str,
    build_leader: bool,
    endpoint_leader: bool,
    connect: Callable[[str], Awaitable[Any]] | None = None,
) -> None:
    connector = connect or _postgres_connect
    conn = await connector(settings.postgres_dsn)
    try:
        result = await conn.execute(
            """
            update deployer_runtime_state
            set leader_instance_id = $3,
                leader_heartbeat_at = now(),
                build_leader = $4,
                endpoint_leader = $5,
                updated_at = now()
            where deployment_id = $1 and base_image_name = $2 and base_image_id = $6
            """,
            settings.deployment_id,
            settings.deployer_backend_base_image,
            instance_id,
            build_leader,
            endpoint_leader,
            base_image_id,
        )
        if not str(result).endswith(" 1"):
            raise RuntimeError(
                "registered immutable base image changed before heartbeat"
            )
    finally:
        await conn.close()


def write_liveness(path: str | Path, *, now: datetime | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"observed_at": (now or datetime.now(timezone.utc)).isoformat()}
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)


def check_liveness(
    path: str | Path,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
) -> PreflightReport:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(payload["observed_at"]))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise ValueError("stale liveness heartbeat")
        check = PreflightCheck(
            "process_liveness", True, "deployer process heartbeat is fresh"
        )
    except Exception:
        check = PreflightCheck(
            "process_liveness",
            False,
            "deployer process heartbeat is missing or stale",
            "Restart the deployer and inspect its startup diagnostics.",
        )
    return PreflightReport(checks=(check,))
