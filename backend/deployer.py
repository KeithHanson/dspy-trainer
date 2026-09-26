from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
from contextlib import suppress
from uuid import uuid4

from app.config import DeployerSettings, get_deployer_settings
from app.deployer_preflight import (
    check_liveness,
    publish_leader_heartbeat,
    require_ready,
    run_deployer_preflight,
    write_liveness,
)
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
_LIVENESS_PATH = "/tmp/dspy-trainer/deployer-live.json"


def _print_report(report: object) -> None:
    payload = report.public_payload()  # type: ignore[attr-defined]
    print(json.dumps(payload, sort_keys=True), flush=True)


async def _heartbeat_loop(
    settings: DeployerSettings,
    *,
    instance_id: str,
    base_image_id: str,
    coordinator: RevisionImageBuildCoordinator,
    endpoint_reconciler: EndpointContainerReconciler,
    stopping: asyncio.Event,
) -> None:
    interval = max(1.0, settings.deployer_leader_timeout_seconds / 3)
    while not stopping.is_set():
        write_liveness(_LIVENESS_PATH)
        try:
            await publish_leader_heartbeat(
                settings,
                instance_id=instance_id,
                base_image_id=base_image_id,
                build_leader=coordinator.is_leader,
                endpoint_leader=endpoint_reconciler.is_leader,
            )
        except Exception:
            logger.error(
                "deployer leadership heartbeat failed; readiness remains false until database state recovers"
            )
        try:
            await asyncio.wait_for(stopping.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_deployer(
    settings: DeployerSettings,
    *,
    base_image_id: str,
) -> None:
    instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
    store = PostgresRevisionImageBuildStore(
        postgres_dsn=settings.postgres_dsn,
        instance_id=instance_id,
        deployment_id=settings.deployment_id,
        base_image_id=base_image_id,
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
    stopping = asyncio.Event()

    def request_stop() -> None:
        stopping.set()
        coordinator.request_stop()
        endpoint_reconciler.request_stop()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, request_stop)

    logger.info(
        "revision image deployer starting instance_id=%s deployment_id=%s project=%s network=%s base_image_id=%s",
        instance_id,
        settings.deployment_id,
        settings.compose_project_name,
        settings.compose_network_name,
        base_image_id,
    )
    heartbeat = asyncio.create_task(
        _heartbeat_loop(
            settings,
            instance_id=instance_id,
            base_image_id=base_image_id,
            coordinator=coordinator,
            endpoint_reconciler=endpoint_reconciler,
            stopping=stopping,
        )
    )
    try:
        await asyncio.gather(coordinator.run(), endpoint_reconciler.run())
    finally:
        stopping.set()
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    logger.info("revision image deployer stopped instance_id=%s", instance_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DSPy Trainer internal deployment controller"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="validate and register deployment prerequisites",
    )
    mode.add_argument(
        "--readiness",
        action="store_true",
        help="verify prerequisites and active controller leadership",
    )
    mode.add_argument(
        "--liveness",
        action="store_true",
        help="verify the controller process heartbeat",
    )
    parser.add_argument(
        "--accept-base-image",
        action="store_true",
        help="explicitly accept a changed local backend image after active builds drain",
    )
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> int:
    settings = get_deployer_settings()
    if args.liveness:
        report = check_liveness(
            _LIVENESS_PATH,
            max_age_seconds=max(2.0, settings.deployer_leader_timeout_seconds * 2),
        )
        _print_report(report)
        return 0 if report.ok else 1

    report = await run_deployer_preflight(
        settings,
        register_base_image=not args.readiness,
        accept_base_image_change=args.accept_base_image,
        require_leader_heartbeat=args.readiness,
    )
    if args.preflight or args.readiness:
        _print_report(report)
        return 0 if report.ok else 1
    if not report.ok:
        _print_report(report)
        return 1

    await run_deployer(settings, base_image_id=require_ready(report))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(asyncio.run(_main(_parse_args())))
