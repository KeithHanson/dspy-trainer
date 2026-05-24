import asyncio
import json
import logging

from app.config import get_settings
from app.services import AppServices


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
logger = logging.getLogger(__name__)


async def process_job(raw_payload: str) -> None:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {"raw": raw_payload}
    logger.info("Pulled job from queue: %s", payload)


async def run_worker() -> None:
    settings = get_settings()
    services = AppServices(settings)
    await services.connect()
    logger.info("Worker started; waiting on queue '%s'", settings.queue_name)
    try:
        while True:
            result = await services.redis.execute_command("BRPOP", settings.queue_name, 5) if services.redis else None
            if result is None:
                continue
            _, raw_payload = result
            await process_job(raw_payload)
    finally:
        await services.disconnect()


if __name__ == "__main__":
    asyncio.run(run_worker())
