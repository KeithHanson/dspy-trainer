from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from uuid import uuid4

from app.config import get_deployer_settings
from app.revision_image_build_process import (
    MultiprocessingRevisionImageBuildChildFactory,
    RevisionImageBuildProcessRunner,
)
from app.revision_image_coordinator import (
    PostgresRevisionImageBuildStore,
    RevisionImageBuildCoordinator,
)

logger = logging.getLogger(__name__)


async def run_deployer() -> None:
    settings = get_deployer_settings()
    instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
    store = PostgresRevisionImageBuildStore(
        postgres_dsn=settings.postgres_dsn,
        instance_id=instance_id,
        deployment_id=settings.deployment_id,
        base_image_id=settings.deployer_backend_base_image_id,
        image_repository=settings.deployer_image_repository,
        platform_version=settings.deployer_platform_version,
        build_log_max_bytes=settings.deployer_build_log_max_bytes,
        leader_timeout_seconds=settings.deployer_leader_timeout_seconds,
    )
    builder = RevisionImageBuildProcessRunner(
        MultiprocessingRevisionImageBuildChildFactory(),
        max_log_bytes=settings.deployer_build_log_max_bytes,
    )
    coordinator = RevisionImageBuildCoordinator(
        store=store,
        builder=builder,
        deployment_id=settings.deployment_id,
        image_repository=settings.deployer_image_repository,
        platform_version=settings.deployer_platform_version,
        claim_timeout_seconds=settings.deployer_claim_timeout_seconds,
        poll_interval_seconds=settings.deployer_poll_interval_seconds,
        build_log_max_bytes=settings.deployer_build_log_max_bytes,
    )

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, coordinator.request_stop)

    logger.info(
        "revision image deployer starting instance_id=%s deployment_id=%s project=%s network=%s",
        instance_id,
        settings.deployment_id,
        settings.compose_project_name,
        settings.compose_network_name,
    )
    await coordinator.run()
    logger.info("revision image deployer stopped instance_id=%s", instance_id)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_deployer())
