import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import sys
import uuid

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import get_settings
from app.revision_image_builder import BUNDLE_IMAGE_PATH
from app.services import AppServices

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [endpoint-worker] %(message)s"
)
logger = logging.getLogger(__name__)


_UNSET = object()


def load_endpoint_worker_boot_identity(
    environ: dict[str, str] | None = None,
) -> dict[str, str | None]:
    source = environ if environ is not None else os.environ
    execution_mode = str(
        source.get("DSPY_TRAINER_ENDPOINT_WORKER_MODE") or "legacy_static"
    ).strip()
    if execution_mode == "legacy_static":
        return {
            "execution_mode": execution_mode,
            "endpoint_id": None,
            "build_id": None,
            "revision_id": None,
            "bundle_path": None,
        }
    if execution_mode != "managed_image":
        raise RuntimeError(
            f"unsupported endpoint worker execution mode: {execution_mode}"
        )
    identity = {
        "execution_mode": execution_mode,
        "endpoint_id": str(source.get("DSPY_TRAINER_ENDPOINT_ID") or "").strip(),
        "build_id": str(source.get("DSPY_TRAINER_BAKED_BUILD_ID") or "").strip(),
        "revision_id": str(source.get("DSPY_TRAINER_BAKED_REVISION_ID") or "").strip(),
        "bundle_path": str(source.get("DSPY_TRAINER_BUNDLE_PATH") or "").strip(),
    }
    missing = sorted(
        name
        for name, value in identity.items()
        if name != "execution_mode" and not value
    )
    if missing:
        raise RuntimeError(
            f"managed endpoint worker identity is missing: {', '.join(missing)}"
        )
    if identity["bundle_path"] != BUNDLE_IMAGE_PATH:
        raise RuntimeError(
            f"managed endpoint worker bundle path must be {BUNDLE_IMAGE_PATH}"
        )
    if not Path(str(identity["bundle_path"])).is_dir():
        raise RuntimeError(
            f"managed endpoint worker baked bundle is unavailable: {identity['bundle_path']}"
        )
    return identity


def _build_runtime_identity(
    *, explicit_worker_id: str | None = None
) -> dict[str, object]:
    boot_identity = load_endpoint_worker_boot_identity()
    hostname = socket.gethostname()
    pid = os.getpid()
    runtime_instance_id = str(uuid.uuid4())
    booted_at = datetime.now(timezone.utc).isoformat()
    session_id = str(os.getenv("PI_SESSION_ID") or "").strip() or None
    return {
        "worker_id": str(explicit_worker_id or "").strip() or None,
        "runtime_instance_id": runtime_instance_id,
        "hostname": hostname,
        "pid": pid,
        "booted_at": booted_at,
        "session_id": session_id,
        "boot_identity": boot_identity,
        "runtime_metadata": {
            "booted_at": booted_at,
            "hostname": hostname,
            "pid": pid,
            "session_id": session_id,
            "platform": {
                "python_executable": os.path.abspath(sys.executable),
                "argv": list(sys.argv),
            },
            "execution_mode": boot_identity["execution_mode"],
            "endpoint_id": boot_identity["endpoint_id"],
            "desired_build_id": boot_identity["build_id"],
            "desired_revision_id": boot_identity["revision_id"],
            "baked_build_id": boot_identity["build_id"],
            "baked_revision_id": boot_identity["revision_id"],
            "bundle_path": boot_identity["bundle_path"],
        },
    }


def _heartbeat_runtime_metadata(
    runtime_identity: dict[str, object],
    *,
    desired_build_id: object = _UNSET,
    warmed_build_id: object = _UNSET,
    desired_revision_id: object = _UNSET,
    warmed_revision_id: object = _UNSET,
    endpoint_id: object = _UNSET,
) -> dict[str, object]:
    runtime_metadata = dict(runtime_identity.get("runtime_metadata") or {})
    heartbeat_state = dict(runtime_identity.get("heartbeat_state") or {})

    def _resolve_field(name: str, value: object) -> object:
        return (
            heartbeat_state.get(name, runtime_metadata.get(name))
            if value is _UNSET
            else value
        )

    fields = {
        "endpoint_id": _resolve_field("endpoint_id", endpoint_id),
        "desired_build_id": _resolve_field("desired_build_id", desired_build_id),
        "warmed_build_id": _resolve_field("warmed_build_id", warmed_build_id),
        "desired_revision_id": _resolve_field(
            "desired_revision_id", desired_revision_id
        ),
        "warmed_revision_id": _resolve_field("warmed_revision_id", warmed_revision_id),
    }
    heartbeat_state.update(fields)
    runtime_identity["heartbeat_state"] = heartbeat_state
    runtime_metadata.update(
        {
            "booted_at": runtime_identity.get("booted_at"),
            "hostname": runtime_identity.get("hostname"),
            "pid": runtime_identity.get("pid"),
            "session_id": runtime_identity.get("session_id"),
            **fields,
        }
    )
    runtime_identity["runtime_metadata"] = runtime_metadata
    return runtime_metadata


def resolve_endpoint_worker_id(
    explicit_worker_id: str | None = None,
    *,
    hostname: str | None = None,
    pid: int | None = None,
) -> str:
    configured_worker_id = str(explicit_worker_id or "").strip()
    if configured_worker_id:
        return configured_worker_id
    resolved_hostname = str(hostname or socket.gethostname()).strip()
    return f"{resolved_hostname}-{pid if pid is not None else os.getpid()}"


async def _heartbeat(
    services: AppServices,
    worker_id: str,
    status: str,
    *,
    task_id: str | None = None,
    endpoint_id: object = _UNSET,
    desired_build_id: object = _UNSET,
    warmed_build_id: object = _UNSET,
    desired_revision_id: object = _UNSET,
    warmed_revision_id: object = _UNSET,
    runtime_identity: dict[str, object] | None = None,
    registration: bool = False,
    last_error: str | None = None,
) -> str:
    effective_runtime_identity = runtime_identity or {}
    if services.postgres_pool is None:
        raise RuntimeError("endpoint workers require database-backed registry")
    payload = {
        "runtime_instance_id": str(
            effective_runtime_identity.get("runtime_instance_id") or ""
        ).strip()
        or None,
        "status": status,
        "assigned_endpoint_id": endpoint_id if endpoint_id is not _UNSET else None,
        "task_id": task_id,
        "hostname": str(effective_runtime_identity.get("hostname") or "").strip()
        or None,
        "pid": (
            effective_runtime_identity.get("pid")
            if isinstance(effective_runtime_identity.get("pid"), int)
            else None
        ),
        "runtime_metadata": _heartbeat_runtime_metadata(
            effective_runtime_identity,
            desired_build_id=desired_build_id,
            warmed_build_id=warmed_build_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
            endpoint_id=endpoint_id,
        ),
        "last_error": last_error,
    }
    if registration:
        response = await services.register_endpoint_worker(
            worker_id=worker_id if str(worker_id or "").strip() else None,
            **payload,
        )
    else:
        response = await services.heartbeat_endpoint_worker(worker_id, **payload)
    if response is None:
        raise RuntimeError(
            f"endpoint worker registration not found for heartbeat: {worker_id}"
        )
    return str(response.get("worker_id") or worker_id)


async def _heartbeat_loop(
    services: AppServices,
    worker_id: str,
    status: str,
    *,
    task_id: str | None = None,
    endpoint_id: object = _UNSET,
    desired_build_id: object = _UNSET,
    warmed_build_id: object = _UNSET,
    desired_revision_id: object = _UNSET,
    warmed_revision_id: object = _UNSET,
    runtime_identity: dict[str, object] | None = None,
) -> None:
    while True:
        await _heartbeat(
            services,
            worker_id,
            status,
            task_id=task_id,
            endpoint_id=endpoint_id,
            desired_build_id=desired_build_id,
            warmed_build_id=warmed_build_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
            runtime_identity=runtime_identity,
        )
        await asyncio.sleep(5)


async def process_endpoint_job(
    services: AppServices,
    raw_payload: str,
    worker_id: str,
    ready_target: dict[str, str | None],
    runtime_identity: dict[str, object] | None = None,
) -> None:
    payload = json.loads(raw_payload)
    invocation_id = str(payload.get("invocation_id") or "").strip()
    endpoint_id = str(ready_target.get("endpoint_id") or "")
    if payload.get("type") != "endpoint_invocation" or not invocation_id:
        logger.error("Invalid endpoint job payload: %s", payload)
        return
    expected = {
        "endpoint_id": endpoint_id,
        "execution_mode": ready_target.get("execution_mode"),
        "build_id": ready_target.get("build_id"),
        "revision_id": ready_target.get("revision_id"),
        "bundle_path": ready_target.get("bundle_path"),
    }
    actual = {
        "endpoint_id": str(payload.get("endpoint_id") or "").strip() or None,
        "execution_mode": str(payload.get("execution_mode") or "").strip() or None,
        "build_id": str(payload.get("build_id") or "").strip() or None,
        "revision_id": str(payload.get("revision_id") or "").strip() or None,
        "bundle_path": str(payload.get("bundle_path") or "").strip() or None,
    }
    if actual != expected:
        logger.error(
            "Rejected endpoint job with worker assignment mismatch: expected=%s actual=%s",
            expected,
            actual,
        )
        await services.publish_endpoint_invocation_event(
            invocation_id,
            "error",
            {
                "error": "worker assignment mismatch",
                "code": "worker_assignment_mismatch",
            },
        )
        return
    heartbeat_task = None
    try:
        await _heartbeat(
            services,
            worker_id,
            "running",
            task_id=invocation_id,
            endpoint_id=endpoint_id,
            desired_build_id=expected["build_id"],
            warmed_build_id=expected["build_id"],
            desired_revision_id=expected["revision_id"],
            warmed_revision_id=expected["revision_id"],
            runtime_identity=runtime_identity,
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                services,
                worker_id,
                "running",
                task_id=invocation_id,
                endpoint_id=endpoint_id,
                desired_build_id=expected["build_id"],
                warmed_build_id=expected["build_id"],
                desired_revision_id=expected["revision_id"],
                warmed_revision_id=expected["revision_id"],
                runtime_identity=runtime_identity,
            )
        )
        await services.run_endpoint_invocation_job(
            invocation_id,
            endpoint_id,
            (
                payload.get("input_payload")
                if isinstance(payload.get("input_payload"), dict)
                else {}
            ),
            worker_id,
            stream=bool(payload.get("stream", False)),
            execution_mode=str(expected["execution_mode"]),
            build_id=expected["build_id"],
            revision_id=expected["revision_id"],
            bundle_path=expected["bundle_path"],
        )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_build_id=expected["build_id"],
            warmed_build_id=expected["build_id"],
            desired_revision_id=expected["revision_id"],
            warmed_revision_id=expected["revision_id"],
            runtime_identity=runtime_identity,
        )


async def ensure_endpoint_assignment_ready(
    services: AppServices,
    worker_id: str,
    assignment: dict[str, object],
    warmed_target: dict[str, str | None] | None = None,
    runtime_identity: dict[str, object] | None = None,
) -> dict[str, str | None] | None:
    endpoint_id = str(assignment.get("endpoint_id") or "").strip()
    execution_mode = str(assignment.get("execution_mode") or "legacy_static").strip()
    if execution_mode == "managed_image":
        boot_identity = dict((runtime_identity or {}).get("boot_identity") or {})
        expected = {
            "execution_mode": "managed_image",
            "endpoint_id": endpoint_id,
            "build_id": str(assignment.get("build_id") or "").strip() or None,
            "revision_id": str(assignment.get("revision_id") or "").strip() or None,
            "bundle_path": str(assignment.get("bundle_path") or "").strip() or None,
        }
        actual = {
            "execution_mode": boot_identity.get("execution_mode"),
            "endpoint_id": boot_identity.get("endpoint_id"),
            "build_id": boot_identity.get("build_id"),
            "revision_id": boot_identity.get("revision_id"),
            "bundle_path": boot_identity.get("bundle_path"),
        }
        if expected != actual:
            await _heartbeat(
                services,
                worker_id,
                "failed",
                endpoint_id=endpoint_id,
                desired_build_id=expected["build_id"],
                warmed_build_id=None,
                desired_revision_id=expected["revision_id"],
                warmed_revision_id=None,
                runtime_identity=runtime_identity,
                last_error="managed_image_assignment_mismatch",
            )
            return None
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_build_id=expected["build_id"],
            warmed_build_id=expected["build_id"],
            desired_revision_id=expected["revision_id"],
            warmed_revision_id=expected["revision_id"],
            runtime_identity=runtime_identity,
        )
        return expected

    deployment = await services.get_endpoint_deployment(endpoint_id)
    if deployment is None or str(deployment.get("phase") or "") != "legacy_static":
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            runtime_identity=runtime_identity,
            last_error="legacy_static_not_enabled",
        )
        return None
    endpoint = await services.get_bundle_endpoint(endpoint_id)
    if endpoint is None:
        await _heartbeat(services, worker_id, "idle", runtime_identity=runtime_identity)
        return None
    module_state = await services.resolve_module_execution_state(
        str(endpoint["module_import_id"])
    )
    if module_state is None:
        await _heartbeat(services, worker_id, "idle", runtime_identity=runtime_identity)
        return None
    desired_revision_id = (
        str(module_state.get("bundle_revision_id") or "").strip() or None
    )
    if desired_revision_id is None:
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            desired_revision_id=None,
            warmed_revision_id=(
                warmed_target.get("revision_id") if warmed_target else None
            ),
            runtime_identity=runtime_identity,
            last_error="revision_metadata_missing",
        )
        return None
    target = {
        "execution_mode": "legacy_static",
        "endpoint_id": endpoint_id,
        "build_id": None,
        "revision_id": desired_revision_id,
        "bundle_path": None,
    }
    try:
        if warmed_target != target:
            await _heartbeat(
                services,
                worker_id,
                "preparing",
                endpoint_id=endpoint_id,
                desired_revision_id=desired_revision_id,
                warmed_revision_id=(
                    warmed_target.get("revision_id") if warmed_target else None
                ),
                runtime_identity=runtime_identity,
            )
            preparing_heartbeat_task = asyncio.create_task(
                _heartbeat_loop(
                    services,
                    worker_id,
                    "preparing",
                    endpoint_id=endpoint_id,
                    desired_revision_id=desired_revision_id,
                    warmed_revision_id=(
                        warmed_target.get("revision_id") if warmed_target else None
                    ),
                    runtime_identity=runtime_identity,
                )
            )
            try:
                await services.ensure_bundle_requirements_installed(
                    module_state["bundle_path"]
                )
            finally:
                preparing_heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await preparing_heartbeat_task
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=desired_revision_id,
            runtime_identity=runtime_identity,
        )
        return target
    except Exception:
        logger.exception(
            "Legacy endpoint worker warmup failed for endpoint %s", endpoint_id
        )
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=(
                warmed_target.get("revision_id") if warmed_target else None
            ),
            runtime_identity=runtime_identity,
            last_error="warmup_failed",
        )
        return None


async def run_endpoint_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    await services.connect()
    explicit_worker_id = str(os.getenv("DSPY_TRAINER_WORKER_ID") or "").strip() or None
    runtime_identity = _build_runtime_identity(explicit_worker_id=explicit_worker_id)
    worker_id = await _heartbeat(
        services,
        resolve_endpoint_worker_id(
            explicit_worker_id, hostname=socket.gethostname(), pid=os.getpid()
        ),
        "idle",
        runtime_identity=runtime_identity,
        registration=True,
    )
    runtime_identity["worker_id"] = worker_id
    logger.info(
        "Endpoint worker started worker_id=%s runtime_instance_id=%s",
        worker_id,
        runtime_identity.get("runtime_instance_id"),
    )
    warmed_target: dict[str, str | None] | None = None
    try:
        while True:
            assignment = await services.get_endpoint_worker_assignment(worker_id)
            endpoint_id = (
                str(assignment.get("endpoint_id") or "").strip() if assignment else ""
            )
            if not endpoint_id:
                warmed_target = None
                await _heartbeat(
                    services, worker_id, "idle", runtime_identity=runtime_identity
                )
                await asyncio.sleep(2)
                continue
            ready_target = await ensure_endpoint_assignment_ready(
                services,
                worker_id,
                assignment,
                warmed_target,
                runtime_identity=runtime_identity,
            )
            if ready_target is None:
                warmed_target = None
                await asyncio.sleep(2)
                continue
            warmed_target = ready_target
            queue_name = services._endpoint_queue_name(
                endpoint_id,
                build_id=ready_target.get("build_id"),
                revision_id=(
                    ready_target.get("revision_id")
                    if ready_target.get("build_id")
                    else None
                ),
            )
            try:
                result = (
                    await services.redis.execute_command("BRPOP", queue_name, 5)
                    if services.redis
                    else None
                )
            except RedisTimeoutError:
                continue
            if result is None:
                continue
            _, raw_payload = result
            try:
                await process_endpoint_job(
                    services,
                    raw_payload,
                    worker_id,
                    ready_target,
                    runtime_identity=runtime_identity,
                )
            except Exception:
                logger.exception("Endpoint worker job processing failed")
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_endpoint_worker())
