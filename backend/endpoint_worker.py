import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import logging
import os
import socket
import sys
import uuid

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import get_settings
from app.executor.module_runner import BundleRuntime, warm_bundle_runtime
from app.services import AppServices


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [endpoint-worker] %(message)s")
logger = logging.getLogger(__name__)


_UNSET = object()


def _build_runtime_identity(*, explicit_worker_id: str | None = None) -> dict[str, object]:
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
        "runtime_metadata": {
            "booted_at": booted_at,
            "hostname": hostname,
            "pid": pid,
            "session_id": session_id,
            "platform": {
                "python_executable": os.path.abspath(sys.executable),
                "argv": list(sys.argv),
            },
        },
    }


def _heartbeat_runtime_metadata(
    runtime_identity: dict[str, object],
    *,
    desired_revision_id: object = _UNSET,
    warmed_revision_id: object = _UNSET,
    endpoint_id: object = _UNSET,
) -> dict[str, object]:
    runtime_metadata = dict(runtime_identity.get("runtime_metadata") or {})
    heartbeat_state = dict(runtime_identity.get("heartbeat_state") or {})

    def _resolve_field(name: str, value: object) -> object:
        return heartbeat_state.get(name) if value is _UNSET else value

    resolved_endpoint_id = _resolve_field("endpoint_id", endpoint_id)
    resolved_desired_revision_id = _resolve_field("desired_revision_id", desired_revision_id)
    resolved_warmed_revision_id = _resolve_field("warmed_revision_id", warmed_revision_id)
    heartbeat_state.update(
        {
            "endpoint_id": resolved_endpoint_id,
            "desired_revision_id": resolved_desired_revision_id,
            "warmed_revision_id": resolved_warmed_revision_id,
        }
    )
    runtime_identity["heartbeat_state"] = heartbeat_state
    runtime_metadata.update(
        {
            "booted_at": runtime_identity.get("booted_at"),
            "hostname": runtime_identity.get("hostname"),
            "pid": runtime_identity.get("pid"),
            "session_id": runtime_identity.get("session_id"),
            "endpoint_id": resolved_endpoint_id,
            "desired_revision_id": resolved_desired_revision_id,
            "warmed_revision_id": resolved_warmed_revision_id,
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
    desired_revision_id: object = _UNSET,
    warmed_revision_id: object = _UNSET,
    runtime_identity: dict[str, object] | None = None,
    registration: bool = False,
    last_error: str | None = None,
) -> str:
    effective_runtime_identity = runtime_identity or {}
    if services.postgres_pool is not None:
        payload = {
            "runtime_instance_id": str(effective_runtime_identity.get("runtime_instance_id") or "").strip() or None,
            "status": status,
            "assigned_endpoint_id": endpoint_id if endpoint_id is not _UNSET else None,
            "task_id": task_id,
            "hostname": str(effective_runtime_identity.get("hostname") or "").strip() or None,
            "pid": effective_runtime_identity.get("pid") if isinstance(effective_runtime_identity.get("pid"), int) else None,
            "runtime_metadata": _heartbeat_runtime_metadata(
                effective_runtime_identity,
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
            raise RuntimeError(f"endpoint worker registration not found for heartbeat: {worker_id}")
        return str(response.get("worker_id") or worker_id)
    raise RuntimeError("endpoint workers require database-backed registry")


async def _heartbeat_loop(
    services: AppServices,
    worker_id: str,
    status: str,
    *,
    task_id: str | None = None,
    endpoint_id: object = _UNSET,
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
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
            runtime_identity=runtime_identity,
        )
        await asyncio.sleep(5)


async def process_endpoint_job(
    services: AppServices,
    raw_payload: str,
    worker_id: str,
    endpoint_id: str,
    revision_id: str | None = None,
    runtime_identity: dict[str, object] | None = None,
    warmed_runtime: BundleRuntime | None = None,
    runtime_env: dict[str, str] | None = None,
) -> None:
    payload = json.loads(raw_payload)
    invocation_id = str(payload.get("invocation_id") or "").strip()
    if payload.get("type") != "endpoint_invocation" or not invocation_id:
        logger.error("Invalid endpoint job payload: %s", payload)
        return
    heartbeat_task = None
    try:
        await _heartbeat(
            services,
            worker_id,
            "running",
            task_id=invocation_id,
            endpoint_id=endpoint_id,
            desired_revision_id=revision_id,
            warmed_revision_id=revision_id,
            runtime_identity=runtime_identity,
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                services,
                worker_id,
                "running",
                task_id=invocation_id,
                endpoint_id=endpoint_id,
                desired_revision_id=revision_id,
                warmed_revision_id=revision_id,
                runtime_identity=runtime_identity,
            )
        )
        await services.run_endpoint_invocation_job(
            invocation_id,
            endpoint_id,
            payload.get("input_payload") if isinstance(payload.get("input_payload"), dict) else {},
            worker_id,
            stream=bool(payload.get("stream", False)),
            warmed_runtime=warmed_runtime,
            runtime_env_override=runtime_env,
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
            desired_revision_id=revision_id,
            warmed_revision_id=revision_id,
            runtime_identity=runtime_identity,
        )


async def ensure_endpoint_assignment_ready(
    services: AppServices,
    worker_id: str,
    endpoint_id: str,
    warmed_revision_id: str | None = None,
    runtime_identity: dict[str, object] | None = None,
    warmed_runtime: BundleRuntime | None = None,
) -> tuple[str | None, BundleRuntime | None, dict[str, str] | None]:
    execution_state = await services.resolve_bundle_endpoint_execution_state(endpoint_id)
    if execution_state is None:
        logger.error("Assigned endpoint module not found: %s", endpoint_id)
        await _heartbeat(services, worker_id, "idle", runtime_identity=runtime_identity)
        return None, None, None
    desired_revision_id = str(execution_state.get("bundle_revision_id") or "").strip() or None
    if desired_revision_id is None:
        logger.error("Assigned endpoint revision metadata missing: %s", endpoint_id)
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            desired_revision_id=None,
            warmed_revision_id=warmed_revision_id,
            runtime_identity=runtime_identity,
            last_error="revision_metadata_missing",
        )
        return None, None, None
    runtime_env = await services.get_module_runtime_environment(str(execution_state.get("module_id") or ""))
    endpoint = await services.get_bundle_endpoint(endpoint_id)
    lm_profile = await services._get_lm_profile_record(str(endpoint["lm_profile_id"]), include_secret=True) if endpoint and endpoint.get("lm_profile_id") else None
    try:
        next_runtime = warmed_runtime
        if warmed_revision_id != desired_revision_id or warmed_runtime is None:
            await _heartbeat(
                services,
                worker_id,
                "preparing",
                endpoint_id=endpoint_id,
                desired_revision_id=desired_revision_id,
                warmed_revision_id=warmed_revision_id,
                runtime_identity=runtime_identity,
            )
            await services.ensure_bundle_requirements_installed(execution_state["bundle_path"])
            next_runtime = await asyncio.to_thread(
                warm_bundle_runtime,
                execution_state["bundle_path"],
                lm_profile,
                runtime_env,
            )
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=desired_revision_id,
            runtime_identity=runtime_identity,
        )
        return desired_revision_id, next_runtime, runtime_env
    except Exception:
        logger.exception("Endpoint worker warmup failed for endpoint %s", endpoint_id)
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
            runtime_identity=runtime_identity,
            last_error="warmup_failed",
        )
        return None, None, None


async def run_endpoint_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    await services.connect()
    runtime_identity = _build_runtime_identity()
    worker_id = await _heartbeat(
        services,
        resolve_endpoint_worker_id(None, hostname=socket.gethostname(), pid=os.getpid()),
        "idle",
        runtime_identity=runtime_identity,
        registration=True,
    )
    runtime_identity["worker_id"] = worker_id
    logger.info("Endpoint worker started")
    logger.info("Endpoint worker id: %s", worker_id)
    logger.info("Endpoint worker runtime instance id: %s", runtime_identity.get("runtime_instance_id"))
    assigned_endpoint_id = ""
    warmed_revision_id: str | None = None
    warmed_runtime: BundleRuntime | None = None
    warmed_runtime_env: dict[str, str] | None = None
    try:
        while True:
            assignment = await services.get_endpoint_worker_assignment(worker_id)
            endpoint_id = str(assignment.get("endpoint_id") or "").strip() if assignment else ""
            if not endpoint_id:
                assigned_endpoint_id = ""
                warmed_revision_id = None
                warmed_runtime = None
                warmed_runtime_env = None
                await _heartbeat(services, worker_id, "idle", runtime_identity=runtime_identity)
                await asyncio.sleep(2)
                continue
            ready_revision_id, warmed_runtime, warmed_runtime_env = await ensure_endpoint_assignment_ready(
                services,
                worker_id,
                endpoint_id,
                warmed_revision_id if endpoint_id == assigned_endpoint_id else None,
                runtime_identity=runtime_identity,
                warmed_runtime=warmed_runtime if endpoint_id == assigned_endpoint_id else None,
            )
            if ready_revision_id is None:
                if endpoint_id != assigned_endpoint_id:
                    warmed_revision_id = None
                    warmed_runtime = None
                    warmed_runtime_env = None
                await asyncio.sleep(2)
                continue
            assigned_endpoint_id = endpoint_id
            warmed_revision_id = ready_revision_id
            try:
                result = await services.redis.execute_command("BRPOP", services._endpoint_queue_name(endpoint_id), 5) if services.redis else None
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
                    endpoint_id,
                    ready_revision_id,
                    runtime_identity=runtime_identity,
                    warmed_runtime=warmed_runtime,
                    runtime_env=warmed_runtime_env,
                )
            except Exception:
                logger.exception("Endpoint worker job processing failed")
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_endpoint_worker())
