import asyncio
import json
import logging

from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import get_settings
from app.services import AppServices


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
logger = logging.getLogger(__name__)


async def process_job(services: AppServices, raw_payload: str) -> None:
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
        result = await services.run_agent_run_task(str(task_id), worker_id="worker")
        logger.info("Processed agent run task: %s", result)


async def run_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    await services.connect()
    logger.info("Worker started; waiting on queue '%s'", settings.queue_name)
    try:
        while True:
            try:
                result = await services.redis.execute_command("BRPOP", settings.queue_name, 5) if services.redis else None
            except RedisTimeoutError:
                continue
            if result is None:
                continue
            _, raw_payload = result
            await process_job(services, raw_payload)
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_worker())
