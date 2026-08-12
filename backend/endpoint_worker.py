import asyncio
from contextlib import suppress
import json
import logging
import os
import socket
import uuid

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import get_settings
from app.services import AppServices


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [endpoint-worker] %(message)s")
logger = logging.getLogger(__name__)

_ENDPOINT_WORKER_CLAIM_TTL_SECONDS = 15


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


def _endpoint_worker_claim_key(services: AppServices, worker_id: str) -> str:
    return f"{services.settings.endpoint_worker_registry_prefix}:claims:{worker_id}"


async def _claim_endpoint_worker_id(services: AppServices, worker_id: str, owner_token: str) -> bool:
    if services.redis is None:
        return True
    return bool(
        await services.redis.set(
            _endpoint_worker_claim_key(services, worker_id),
            owner_token,
            ex=_ENDPOINT_WORKER_CLAIM_TTL_SECONDS,
            nx=True,
        )
    )


async def _refresh_endpoint_worker_claim(services: AppServices, worker_id: str, owner_token: str | None) -> None:
    if services.redis is None or not owner_token:
        return
    claim_key = _endpoint_worker_claim_key(services, worker_id)
    refreshed = await services.redis.set(
        claim_key,
        owner_token,
        ex=_ENDPOINT_WORKER_CLAIM_TTL_SECONDS,
        xx=True,
    )
    if refreshed:
        return
    await services.redis.set(
        claim_key,
        owner_token,
        ex=_ENDPOINT_WORKER_CLAIM_TTL_SECONDS,
        nx=True,
    )


async def resolve_configured_endpoint_worker_id(
    services: AppServices,
    explicit_worker_id: str | None = None,
    *,
    hostname: str | None = None,
    pid: int | None = None,
) -> tuple[str, str | None]:
    owner_token = str(uuid.uuid4())
    configured_worker_id = str(explicit_worker_id or "").strip()
    if configured_worker_id:
        await _claim_endpoint_worker_id(services, configured_worker_id, owner_token)
        return configured_worker_id, owner_token if services.redis is not None else None
    candidate_worker_ids = services.settings.endpoint_worker_ids_list()
    if services.redis is not None and candidate_worker_ids:
        while True:
            for worker_id in candidate_worker_ids:
                if await _claim_endpoint_worker_id(services, worker_id, owner_token):
                    return worker_id, owner_token
            logger.info("No configured endpoint worker id available yet; retrying claim")
            await asyncio.sleep(2)
    return resolve_endpoint_worker_id(None, hostname=hostname, pid=pid), None


async def _heartbeat(
    services: AppServices,
    worker_id: str,
    status: str,
    *,
    task_id: str | None = None,
    endpoint_id: str | None = None,
    desired_revision_id: str | None = None,
    warmed_revision_id: str | None = None,
    claim_owner_token: str | None = None,
) -> None:
    if services.redis is None:
        return
    await _refresh_endpoint_worker_claim(services, worker_id, claim_owner_token)
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
    claim_owner_token: str | None = None,
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
            claim_owner_token=claim_owner_token,
        )
        await asyncio.sleep(5)


async def process_endpoint_job(
    services: AppServices,
    raw_payload: str,
    worker_id: str,
    endpoint_id: str,
    revision_id: str | None = None,
    claim_owner_token: str | None = None,
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
            claim_owner_token=claim_owner_token,
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
                claim_owner_token=claim_owner_token,
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
            claim_owner_token=claim_owner_token,
        )


async def ensure_endpoint_assignment_ready(
    services: AppServices,
    worker_id: str,
    endpoint_id: str,
    warmed_revision_id: str | None = None,
    claim_owner_token: str | None = None,
) -> str | None:
    endpoint = await services.get_bundle_endpoint(endpoint_id)
    if endpoint is None:
        logger.error("Assigned endpoint not found: %s", endpoint_id)
        await _heartbeat(services, worker_id, "idle", claim_owner_token=claim_owner_token)
        return None
    module_state = await services.resolve_module_execution_state(str(endpoint["module_import_id"]))
    if module_state is None:
        logger.error("Assigned endpoint module not found: %s", endpoint_id)
        await _heartbeat(services, worker_id, "idle", claim_owner_token=claim_owner_token)
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
            claim_owner_token=claim_owner_token,
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
                claim_owner_token=claim_owner_token,
            )
            await services.ensure_bundle_requirements_installed(module_state["bundle_path"])
        await _heartbeat(
            services,
            worker_id,
            "listening",
            endpoint_id=endpoint_id,
            desired_revision_id=desired_revision_id,
            warmed_revision_id=desired_revision_id,
            claim_owner_token=claim_owner_token,
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
            claim_owner_token=claim_owner_token,
        )
        return None


async def run_endpoint_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    await services.connect()
    worker_id, claim_owner_token = await resolve_configured_endpoint_worker_id(
        services,
        os.getenv("DSPY_TRAINER_ENDPOINT_WORKER_ID"),
        hostname=socket.gethostname(),
        pid=os.getpid(),
    )
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
                await _heartbeat(services, worker_id, "idle", claim_owner_token=claim_owner_token)
                await asyncio.sleep(2)
                continue
            ready_revision_id = await ensure_endpoint_assignment_ready(
                services,
                worker_id,
                endpoint_id,
                warmed_revision_id if endpoint_id == assigned_endpoint_id else None,
                claim_owner_token=claim_owner_token,
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
                await process_endpoint_job(
                    services,
                    raw_payload,
                    worker_id,
                    endpoint_id,
                    ready_revision_id,
                    claim_owner_token=claim_owner_token,
                )
            except Exception:
                logger.exception("Endpoint worker job processing failed")
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_endpoint_worker())
