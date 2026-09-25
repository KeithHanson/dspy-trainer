from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from uuid import uuid4

from app.config import get_deployer_settings
from app.endpoint_container_reconciler import (
    DockerSdkEndpointAdapter,
    EndpointContainerReconciler,
    PostgresEndpointContainerStore,
)
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
    endpoint_store = PostgresEndpointContainerStore(
        postgres_dsn=settings.postgres_dsn,
        instance_id=instance_id,
        leader_timeout_seconds=settings.deployer_leader_timeout_seconds,
    )
    endpoint_docker = DockerSdkEndpointAdapter(
        deployment_id=settings.deployment_id,
        compose_project=settings.compose_network_project_label,
        label_namespace=settings.managed_label_namespace,
    )
    runtime_environment = {
        "DSPY_TRAINER_POSTGRES_DSN": settings.postgres_dsn,
        "DSPY_TRAINER_REDIS_URL": settings.redis_url,
        "DSPY_TRAINER_QUEUE_NAME": settings.queue_name,
        "DSPY_TRAINER_ENDPOINT_QUEUE_PREFIX": settings.endpoint_queue_prefix,
        "DSPY_TRAINER_ENDPOINT_INVOCATION_CHANNEL_PREFIX": settings.endpoint_invocation_channel_prefix,
        "DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS": str(
            settings.endpoint_worker_heartbeat_ttl_seconds
        ),
        "DSPY_TRAINER_MLFLOW_TRACKING_URI": settings.mlflow_tracking_uri,
        "MLFLOW_TRACKING_URI": settings.mlflow_tracking_uri,
    }
    if settings.module_env_encryption_key:
        runtime_environment["DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY"] = (
            settings.module_env_encryption_key
        )
    endpoint_reconciler = EndpointContainerReconciler(
        store=endpoint_store,
        docker=endpoint_docker,
        deployment_id=settings.deployment_id,
        compose_project=settings.compose_network_project_label,
        network_selector=settings.compose_network_name,
        label_namespace=settings.managed_label_namespace,
        runtime_environment=runtime_environment,
        readiness_timeout_seconds=settings.deployer_endpoint_readiness_timeout_seconds,
        drain_timeout_seconds=settings.deployer_endpoint_drain_timeout_seconds,
        reconcile_interval_seconds=settings.deployer_endpoint_reconcile_interval_seconds,
        image_retention_count=settings.deployer_image_retention_count,
    )

    def request_stop() -> None:
        coordinator.request_stop()
        endpoint_reconciler.request_stop()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, request_stop)

    logger.info(
        "revision image deployer starting instance_id=%s deployment_id=%s project=%s network=%s",
        instance_id,
        settings.deployment_id,
        settings.compose_project_name,
        settings.compose_network_name,
    )
    await asyncio.gather(coordinator.run(), endpoint_reconciler.run())
    logger.info("revision image deployer stopped instance_id=%s", instance_id)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_deployer())
