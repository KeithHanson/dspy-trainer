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


def _normalize_log_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value if value and all(ch not in value for ch in (" ", "\t", "\n", '"', "=")) else json.dumps(value)
    return json.dumps(value, sort_keys=True, default=str)


def _log_worker_event(event: str, /, level: int = logging.INFO, **fields: object) -> None:
    ordered_fields = {"event": event, **fields}
    message = " ".join(f"{key}={_normalize_log_value(value)}" for key, value in ordered_fields.items())
    logger.log(level, message)


def _log_assignment_transition(worker_id: str, previous_endpoint_id: str | None, next_endpoint_id: str | None) -> None:
    previous = str(previous_endpoint_id or "").strip() or None
    current = str(next_endpoint_id or "").strip() or None
    if previous == current:
        return
    if current is None:
        _log_worker_event(
            "endpoint_worker.assignment_lost",
            worker_id=worker_id,
            previous_endpoint_id=previous,
        )
        return
    _log_worker_event(
        "endpoint_worker.assignment_changed",
        worker_id=worker_id,
        previous_endpoint_id=previous,
        endpoint_id=current,
    )


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
    desired_restart_generation: object = _UNSET,
    warmed_restart_generation: object = _UNSET,
    endpoint_id: object = _UNSET,
) -> dict[str, object]:
    runtime_metadata = dict(runtime_identity.get("runtime_metadata") or {})
    heartbeat_state = dict(runtime_identity.get("heartbeat_state") or {})

    def _resolve_field(name: str, value: object) -> object:
        return heartbeat_state.get(name) if value is _UNSET else value

    resolved_endpoint_id = _resolve_field("endpoint_id", endpoint_id)
    resolved_desired_revision_id = _resolve_field("desired_revision_id", desired_revision_id)
    resolved_warmed_revision_id = _resolve_field("warmed_revision_id", warmed_revision_id)
    resolved_desired_restart_generation = _resolve_field("desired_restart_generation", desired_restart_generation)
    resolved_warmed_restart_generation = _resolve_field("warmed_restart_generation", warmed_restart_generation)
    heartbeat_state.update(
        {
            "endpoint_id": resolved_endpoint_id,
            "desired_revision_id": resolved_desired_revision_id,
            "warmed_revision_id": resolved_warmed_revision_id,
            "desired_restart_generation": resolved_desired_restart_generation,
            "warmed_restart_generation": resolved_warmed_restart_generation,
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
            "desired_restart_generation": resolved_desired_restart_generation,
            "warmed_restart_generation": resolved_warmed_restart_generation,
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
    desired_restart_generation: object = _UNSET,
    warmed_restart_generation: object = _UNSET,
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
                desired_restart_generation=desired_restart_generation,
                warmed_restart_generation=warmed_restart_generation,
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
    desired_restart_generation: object = _UNSET,
    warmed_restart_generation: object = _UNSET,
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
            desired_restart_generation=desired_restart_generation,
            warmed_restart_generation=warmed_restart_generation,
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
    restart_generation: int | None = None,
) -> None:
    payload = json.loads(raw_payload)
    invocation_id = str(payload.get("invocation_id") or "").strip()
    if payload.get("type") != "endpoint_invocation" or not invocation_id:
        _log_worker_event(
            "endpoint_worker.invocation_payload_invalid",
            level=logging.ERROR,
            worker_id=worker_id,
            endpoint_id=endpoint_id,
            payload=payload,
        )
        return
    _log_worker_event(
        "endpoint_worker.invocation_started",
        worker_id=worker_id,
        endpoint_id=endpoint_id,
        invocation_id=invocation_id,
        desired_revision_id=revision_id,
        warmed_revision_id=revision_id,
        restart_generation=restart_generation,
        stream=bool(payload.get("stream", False)),
    )
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
            desired_restart_generation=restart_generation,
            warmed_restart_generation=restart_generation,
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
                desired_restart_generation=restart_generation,
                warmed_restart_generation=restart_generation,
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
        _log_worker_event(
            "endpoint_worker.invocation_finished",
            worker_id=worker_id,
            endpoint_id=endpoint_id,
            invocation_id=invocation_id,
            desired_revision_id=revision_id,
            warmed_revision_id=revision_id,
            restart_generation=restart_generation,
        )
    except Exception as exc:
        _log_worker_event(
            "endpoint_worker.invocation_failed",
            level=logging.ERROR,
            worker_id=worker_id,
            endpoint_id=endpoint_id,
            invocation_id=invocation_id,
            desired_revision_id=revision_id,
            warmed_revision_id=revision_id,
            restart_generation=restart_generation,
            error=str(exc),
        )
        raise
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
            desired_restart_generation=restart_generation,
            warmed_restart_generation=restart_generation,
        )


async def ensure_endpoint_assignment_ready(
    services: AppServices,
    worker_id: str,
    endpoint_id: str,
    warmed_revision_id: str | None = None,
    warmed_restart_generation: int | None = None,
    runtime_identity: dict[str, object] | None = None,
    warmed_runtime: BundleRuntime | None = None,
) -> tuple[str | None, int | None, BundleRuntime | None, dict[str, str] | None]:
    execution_state = await services.resolve_bundle_endpoint_execution_state(endpoint_id)
    if execution_state is None:
        _log_worker_event(
            "endpoint_worker.assignment_not_ready",
            level=logging.ERROR,
            worker_id=worker_id,
            endpoint_id=endpoint_id,
            reason="execution_state_missing",
        )
        await _heartbeat(services, worker_id, "idle", runtime_identity=runtime_identity)
        return None, None, None, None
    desired_revision_id = str(execution_state.get("bundle_revision_id") or "").strip() or None
    desired_restart_generation = int(execution_state.get("restart_generation") or 0)
    if desired_revision_id is None:
        _log_worker_event(
            "endpoint_worker.assignment_not_ready",
            level=logging.ERROR,
            worker_id=worker_id,
            endpoint_id=endpoint_id,
            reason="revision_metadata_missing",
            desired_restart_generation=desired_restart_generation,
            warmed_revision_id=warmed_revision_id,
            warmed_restart_generation=warmed_restart_generation,
        )
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            desired_revision_id=None,
            warmed_revision_id=warmed_revision_id,
            runtime_identity=runtime_identity,
            last_error="revision_metadata_missing",
            desired_restart_generation=desired_restart_generation,
            warmed_restart_generation=warmed_restart_generation,
        )
        return None, None, None, None
    runtime_env = await services.get_module_runtime_environment(str(execution_state.get("module_id") or ""))
    endpoint = await services.get_bundle_endpoint(endpoint_id)
    lm_profile = await services._get_lm_profile_record(str(endpoint["lm_profile_id"]), include_secret=True) if endpoint and endpoint.get("lm_profile_id") else None
    reload_reasons: list[str] = []
    if warmed_runtime is None:
        reload_reasons.append("runtime_missing")
    if warmed_revision_id != desired_revision_id:
        reload_reasons.append("revision_changed")
    if warmed_restart_generation != desired_restart_generation:
        reload_reasons.append("restart_generation_changed")
    action = "warmup_required" if reload_reasons else "reuse_warmed_runtime"
    _log_worker_event(
        "endpoint_worker.runtime_decision",
        worker_id=worker_id,
        endpoint_id=endpoint_id,
        action=action,
        desired_revision_id=desired_revision_id,
        warmed_revision_id=warmed_revision_id,
        desired_restart_generation=desired_restart_generation,
        warmed_restart_generation=warmed_restart_generation,
        reasons=reload_reasons,
    )
    try:
        next_runtime = warmed_runtime
        if reload_reasons:
            _log_worker_event(
                "endpoint_worker.warmup_started",
                worker_id=worker_id,
                endpoint_id=endpoint_id,
                desired_revision_id=desired_revision_id,
                warmed_revision_id=warmed_revision_id,
                desired_restart_generation=desired_restart_generation,
                warmed_restart_generation=warmed_restart_generation,
                reasons=reload_reasons,
                bundle_path=execution_state["bundle_path"],
            )
            await _heartbeat(
                services,
                worker_id,
                "preparing",
                endpoint_id=endpoint_id,
                desired_revision_id=desired_revision_id,
                warmed_revision_id=warmed_revision_id,
                runtime_identity=runtime_identity,
                desired_restart_generation=desired_restart_generation,
                warmed_restart_generation=warmed_restart_generation,
            )
            await services.ensure_bundle_requirements_installed(execution_state["bundle_path"])
            next_runtime = await asyncio.to_thread(
                warm_bundle_runtime,
                execution_state["bundle_path"],
                lm_profile,
                runtime_env,
            )
            _log_worker_event(
                "endpoint_worker.warmup_succeeded",
                worker_id=worker_id,
                endpoint_id=endpoint_id,
                desired_revision_id=desired_revision_id,
                warmed_revision_id=desired_revision_id,
                desired_restart_generation=desired_restart_generation,
                warmed_restart_generation=desired_restart_generation,
                reasons=reload_reasons,
            )
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=desired_revision_id,
            runtime_identity=runtime_identity,
            desired_restart_generation=desired_restart_generation,
            warmed_restart_generation=desired_restart_generation,
        )
        return desired_revision_id, desired_restart_generation, next_runtime, runtime_env
    except Exception as exc:
        _log_worker_event(
            "endpoint_worker.warmup_failed",
            level=logging.ERROR,
            worker_id=worker_id,
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
            desired_restart_generation=desired_restart_generation,
            warmed_restart_generation=warmed_restart_generation,
            reasons=reload_reasons,
            error=str(exc),
        )
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
            desired_restart_generation=desired_restart_generation,
            warmed_restart_generation=warmed_restart_generation,
        )
        return None, None, None, None


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
    _log_worker_event(
        "endpoint_worker.started",
        worker_id=worker_id,
        runtime_instance_id=runtime_identity.get("runtime_instance_id"),
        hostname=runtime_identity.get("hostname"),
        pid=runtime_identity.get("pid"),
        session_id=runtime_identity.get("session_id"),
    )
    assigned_endpoint_id = ""
    warmed_revision_id: str | None = None
    warmed_restart_generation: int | None = None
    warmed_runtime: BundleRuntime | None = None
    warmed_runtime_env: dict[str, str] | None = None
    try:
        while True:
            assignment = await services.get_endpoint_worker_assignment(worker_id)
            endpoint_id = str(assignment.get("endpoint_id") or "").strip() if assignment else ""
            if endpoint_id != assigned_endpoint_id:
                _log_assignment_transition(worker_id, assigned_endpoint_id or None, endpoint_id or None)
            if not endpoint_id:
                assigned_endpoint_id = ""
                warmed_revision_id = None
                warmed_restart_generation = None
                warmed_runtime = None
                warmed_runtime_env = None
                await _heartbeat(services, worker_id, "idle", runtime_identity=runtime_identity)
                await asyncio.sleep(2)
                continue
            ready_revision_id, ready_restart_generation, warmed_runtime, warmed_runtime_env = await ensure_endpoint_assignment_ready(
                services,
                worker_id,
                endpoint_id,
                warmed_revision_id if endpoint_id == assigned_endpoint_id else None,
                warmed_restart_generation if endpoint_id == assigned_endpoint_id else None,
                runtime_identity=runtime_identity,
                warmed_runtime=warmed_runtime if endpoint_id == assigned_endpoint_id else None,
            )
            if ready_revision_id is None:
                _log_worker_event(
                    "endpoint_worker.assignment_retry_scheduled",
                    worker_id=worker_id,
                    endpoint_id=endpoint_id,
                    desired_revision_id=None,
                    warmed_revision_id=warmed_revision_id if endpoint_id == assigned_endpoint_id else None,
                    desired_restart_generation=None,
                    warmed_restart_generation=warmed_restart_generation if endpoint_id == assigned_endpoint_id else None,
                    reason="assignment_not_ready",
                    retry_in_seconds=2,
                )
                if endpoint_id != assigned_endpoint_id:
                    warmed_revision_id = None
                    warmed_restart_generation = None
                    warmed_runtime = None
                    warmed_runtime_env = None
                await asyncio.sleep(2)
                continue
            assigned_endpoint_id = endpoint_id
            warmed_revision_id = ready_revision_id
            warmed_restart_generation = ready_restart_generation
            try:
                result = await services.redis.execute_command("BRPOP", services._endpoint_queue_name(endpoint_id), 5) if services.redis else None
            except RedisTimeoutError:
                _log_worker_event(
                    "endpoint_worker.queue_poll_retry",
                    worker_id=worker_id,
                    endpoint_id=endpoint_id,
                    desired_revision_id=ready_revision_id,
                    warmed_revision_id=warmed_revision_id,
                    desired_restart_generation=ready_restart_generation,
                    warmed_restart_generation=warmed_restart_generation,
                    reason="redis_timeout",
                )
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
                    restart_generation=ready_restart_generation,
                )
            except Exception as exc:
                _log_worker_event(
                    "endpoint_worker.job_processing_failed",
                    level=logging.ERROR,
                    worker_id=worker_id,
                    endpoint_id=endpoint_id,
                    desired_revision_id=ready_revision_id,
                    warmed_revision_id=warmed_revision_id,
                    desired_restart_generation=ready_restart_generation,
                    warmed_restart_generation=warmed_restart_generation,
                    error=str(exc),
                )
                logger.exception("Endpoint worker job processing failed")
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_endpoint_worker())
