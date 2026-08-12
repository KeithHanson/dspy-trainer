import asyncio
from contextlib import suppress
import json
import logging
import os
import socket

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import get_settings
from app.services import AppServices


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [endpoint-worker] %(message)s")
logger = logging.getLogger(__name__)


async def _heartbeat(
    services: AppServices,
    worker_id: str,
    status: str,
    *,
    task_id: str | None = None,
    endpoint_id: str | None = None,
    desired_revision_id: str | None = None,
    warmed_revision_id: str | None = None,
) -> None:
    if services.redis is None:
        return
    key = f"{services.settings.endpoint_worker_registry_prefix}:{worker_id}"
    payload = {
        "worker_id": worker_id,
        "status": status,
        "task_id": task_id,
        "endpoint_id": endpoint_id,
        "desired_revision_id": desired_revision_id,
        "warmed_revision_id": warmed_revision_id,
        "kind": "endpoint",
        "last_seen": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    await services.redis.set(key, json.dumps(payload), ex=15)
    inventory_key = f"{services.settings.endpoint_worker_inventory_prefix}:{worker_id}"
    inventory_payload = dict(payload)
    inventory_payload["last_heartbeat_status"] = status
    await services.redis.set(inventory_key, json.dumps(inventory_payload))


async def _heartbeat_loop(
    services: AppServices,
    worker_id: str,
    status: str,
    *,
    task_id: str | None = None,
    endpoint_id: str | None = None,
    desired_revision_id: str | None = None,
    warmed_revision_id: str | None = None,
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
        )
        await asyncio.sleep(5)


async def process_endpoint_job(
    services: AppServices,
    raw_payload: str,
    worker_id: str,
    endpoint_id: str,
    revision_id: str | None = None,
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
            )
        )
        await services.run_endpoint_invocation_job(
            invocation_id,
            endpoint_id,
            payload.get("input_payload") if isinstance(payload.get("input_payload"), dict) else {},
            worker_id,
            stream=bool(payload.get("stream", False)),
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
        )


async def ensure_endpoint_assignment_ready(
    services: AppServices,
    worker_id: str,
    endpoint_id: str,
    warmed_revision_id: str | None = None,
) -> str | None:
    endpoint = await services.get_bundle_endpoint(endpoint_id)
    if endpoint is None:
        logger.error("Assigned endpoint not found: %s", endpoint_id)
        await _heartbeat(services, worker_id, "idle")
        return None
    module_state = await services.resolve_module_execution_state(str(endpoint["module_import_id"]))
    if module_state is None:
        logger.error("Assigned endpoint module not found: %s", endpoint_id)
        await _heartbeat(services, worker_id, "idle")
        return None
    desired_revision_id = str(module_state.get("bundle_revision_id") or "").strip() or None
    if warmed_revision_id != desired_revision_id:
        await _heartbeat(
            services,
            worker_id,
            "stale",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
        )
    try:
        if warmed_revision_id != desired_revision_id:
            await _heartbeat(
                services,
                worker_id,
                "preparing",
                endpoint_id=endpoint_id,
                desired_revision_id=desired_revision_id,
                warmed_revision_id=warmed_revision_id,
            )
            await services.ensure_bundle_requirements_installed(module_state["bundle_path"])
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=desired_revision_id,
        )
        return desired_revision_id
    except Exception:
        logger.exception("Endpoint worker warmup failed for endpoint %s", endpoint_id)
        await _heartbeat(
            services,
            worker_id,
            "failed",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=warmed_revision_id,
        )
        return None


async def run_endpoint_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    worker_id = os.getenv("DSPY_TRAINER_ENDPOINT_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
    await services.connect()
    logger.info("Endpoint worker started")
    logger.info("Endpoint worker id: %s", worker_id)
    assigned_endpoint_id = ""
    warmed_revision_id: str | None = None
    try:
        while True:
            await services.reconcile_endpoint_worker_assignments()
            assignment = await services.get_endpoint_worker_assignment(worker_id)
            endpoint_id = str(assignment.get("endpoint_id") or "").strip() if assignment else ""
            if not endpoint_id:
                assigned_endpoint_id = ""
                warmed_revision_id = None
                await _heartbeat(services, worker_id, "idle")
                await asyncio.sleep(2)
                continue
            ready_revision_id = await ensure_endpoint_assignment_ready(
                services,
                worker_id,
                endpoint_id,
                warmed_revision_id if endpoint_id == assigned_endpoint_id else None,
            )
            if ready_revision_id is None:
                if endpoint_id != assigned_endpoint_id:
                    warmed_revision_id = None
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
                await process_endpoint_job(services, raw_payload, worker_id, endpoint_id, ready_revision_id)
            except Exception:
                logger.exception("Endpoint worker job processing failed")
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_endpoint_worker())
