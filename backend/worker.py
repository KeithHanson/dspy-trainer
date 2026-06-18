import asyncio
from contextlib import suppress
import json
import logging
import os
import socket

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import get_settings
from app.services import AppServices


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
logger = logging.getLogger(__name__)


async def _heartbeat(services: AppServices, worker_id: str, status: str, task_id: str | None = None) -> None:
    if services.redis is None:
        return
    key = f"{services.settings.worker_registry_prefix}:{worker_id}"
    payload = {
        "worker_id": worker_id,
        "status": status,
        "task_id": task_id,
        "last_seen": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    await services.redis.set(key, json.dumps(payload), ex=15)


async def _heartbeat_loop(services: AppServices, worker_id: str, status: str, task_id: str | None = None) -> None:
    while True:
        await _heartbeat(services, worker_id, status, task_id)
        await asyncio.sleep(5)


async def process_job(services: AppServices, raw_payload: str, worker_id: str) -> None:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {"raw": raw_payload}
    logger.info("Pulled job from queue: %s", payload)
    job_type = payload.get("type") if isinstance(payload, dict) else None
    if job_type == "agent_run_task":
        task_id = payload.get("task_id")
        if not task_id:
            logger.error("Missing task_id in agent_run_task payload")
            return
        heartbeat_task = None
        try:
            await _heartbeat(services, worker_id, "running", str(task_id))
            heartbeat_task = asyncio.create_task(_heartbeat_loop(services, worker_id, "running", str(task_id)))
            result = await services.run_agent_run_task(str(task_id), worker_id=worker_id)
            logger.info("Processed agent run task: %s", result)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            await _heartbeat(services, worker_id, "listening")
        return

    if job_type == "optimization_job":
        job_id = payload.get("job_id")
        if not job_id:
            logger.error("Missing job_id in optimization_job payload")
            return
        await services.append_optimization_process_log(
            str(job_id),
            [
                f"worker_id={worker_id}",
                f"worker_picked_up_at={__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}",
                "status=worker_picked_up",
            ],
        )
        heartbeat_task = None
        try:
            await _heartbeat(services, worker_id, "running", str(job_id))
            heartbeat_task = asyncio.create_task(_heartbeat_loop(services, worker_id, "running", str(job_id)))
            result = await services.run_optimization_job(str(job_id))
            logger.info("Processed optimization job: %s", result)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            await _heartbeat(services, worker_id, "listening")


async def run_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    worker_id = os.getenv("DSPY_TRAINER_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
    await services.connect()
    logger.info("Worker started; waiting on queue '%s'", settings.queue_name)
    logger.info("Worker id: %s", worker_id)
    try:
        await _heartbeat(services, worker_id, "listening")
        while True:
            try:
                await _heartbeat(services, worker_id, "listening")
                result = await services.redis.execute_command("BRPOP", settings.queue_name, 5) if services.redis else None
            except RedisTimeoutError:
                continue
            if result is None:
                continue
            _, raw_payload = result
            try:
                await process_job(services, raw_payload, worker_id)
            except Exception:
                logger.exception("Worker job processing failed")
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_worker())
