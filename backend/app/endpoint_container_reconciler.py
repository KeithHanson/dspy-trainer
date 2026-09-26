from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.revision_image_builder import (
    LABEL_BUILD_ID,
    LABEL_MODULE_ID,
    LABEL_REVISION_ID,
    require_managed_revision_image,
)
from app.revision_images import MAX_FAILURE_REASON_CHARS

logger = logging.getLogger(__name__)

_RECONCILER_ADVISORY_LOCK = 0x44535059434F4E54
_CONTAINER_LOG_MAX_BYTES = 65536
MANAGED_CONTAINER_KIND = "endpoint-worker"
PLATFORM_OWNER = "dspy-trainer"


@dataclass(frozen=True)
class DeploymentIntent:
    deployment_id: str
    endpoint_id: str
    module_id: str
    phase: str
    desired_replica_count: int
    rollout_generation: int
    active_build_id: str | None = None
    active_revision_id: str | None = None
    active_image_id: str | None = None
    target_build_id: str | None = None
    target_revision_id: str | None = None
    target_image_id: str | None = None
    previous_build_id: str | None = None
    previous_revision_id: str | None = None
    previous_image_id: str | None = None
    rollout_started_at: datetime | None = None

    def desired_artifact(self) -> tuple[str, str, str] | None:
        if self.phase == "rollback":
            values = (
                self.active_build_id,
                self.active_revision_id,
                self.active_image_id,
            )
        elif self.target_build_id or self.target_revision_id or self.target_image_id:
            values = (
                self.target_build_id,
                self.target_revision_id,
                self.target_image_id,
            )
        else:
            values = (
                self.active_build_id,
                self.active_revision_id,
                self.active_image_id,
            )
        if not all(values):
            return None
        return str(values[0]), str(values[1]), str(values[2])

    @property
    def is_rollout(self) -> bool:
        return self.phase in {"pending", "rolling", "draining", "rollback"}


@dataclass(frozen=True)
class RegistrySnapshot:
    worker_id: str
    status: str
    assigned_endpoint_id: str | None
    reported_endpoint_id: str | None
    execution_mode: str | None
    build_id: str | None
    revision_id: str | None
    deployment_id: str | None
    slot: int | None
    rollout_generation: int | None
    task_id: str | None
    is_live: bool
    last_seen_at: datetime | None = None

    def ready_for(self, identity: ContainerIdentity) -> bool:
        return bool(
            self.is_live
            and self.status in {"listening", "running"}
            and self.assigned_endpoint_id == identity.endpoint_id
            and self.reported_endpoint_id == identity.endpoint_id
            and self.execution_mode == "managed_image"
            and self.build_id == identity.build_id
            and self.revision_id == identity.revision_id
            and self.deployment_id == identity.deployment_id
            and self.slot == identity.slot
            and self.rollout_generation == identity.rollout_generation
        )

    @property
    def active(self) -> bool:
        return bool(self.is_live and (self.status == "running" or self.task_id))


@dataclass(frozen=True)
class ObservedContainer:
    container_id: str
    name: str
    image_id: str
    labels: Mapping[str, str]
    state: str
    created_at: datetime

    @property
    def running(self) -> bool:
        return self.state == "running"


@dataclass(frozen=True)
class ContainerObservation:
    containers: tuple[ObservedContainer, ...]
    complete: bool


@dataclass(frozen=True)
class ContainerRecord:
    container_id: str
    lifecycle: str
    started_at: datetime | None = None
    drain_started_at: datetime | None = None


@dataclass(frozen=True)
class ContainerIdentity:
    owner: str
    compose_project: str
    endpoint_id: str
    deployment_id: str
    slot: int
    build_id: str
    revision_id: str
    rollout_generation: int
    worker_id: str
    image_id: str


@dataclass(frozen=True)
class ContainerLaunchSpec:
    name: str
    worker_id: str
    image_id: str
    module_id: str
    endpoint_id: str
    deployment_id: str
    build_id: str
    revision_id: str
    slot: int
    rollout_generation: int
    network_id: str
    labels: Mapping[str, str]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class ImagePruneCandidate:
    image_id: str
    module_id: str
    build_id: str
    revision_id: str


class EndpointContainerStore(Protocol):
    async def try_acquire_leadership(self) -> bool: ...

    async def release_leadership(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def reconcile_deployment_targets(self) -> int: ...

    async def list_deployment_intents(self) -> list[DeploymentIntent]: ...

    async def list_registry_snapshots(self) -> dict[str, RegistrySnapshot]: ...

    async def list_container_records(self) -> dict[str, ContainerRecord]: ...

    async def observe_container(
        self,
        identity: ContainerIdentity,
        container: ObservedContainer,
        *,
        now: datetime,
    ) -> None: ...

    async def record_container_started(
        self,
        identity: ContainerIdentity,
        container: ObservedContainer,
        *,
        now: datetime,
    ) -> None: ...

    async def mark_container_draining(
        self, container_id: str, *, now: datetime
    ) -> None: ...

    async def record_container_removed(
        self,
        container_id: str,
        *,
        now: datetime,
        timed_out: bool,
        reason: str | None,
        logs: str,
    ) -> None: ...

    async def block_worker_claims(self, worker_id: str) -> None: ...

    async def mark_deployment_rolling(self, deployment_id: str) -> None: ...

    async def fail_or_rollback_deployment(
        self, deployment_id: str, reason: str
    ) -> None: ...

    async def complete_deployment(
        self, deployment_id: str, *, rolled_back: bool
    ) -> None: ...

    async def list_image_prune_candidates(
        self, retention_count: int
    ) -> list[ImagePruneCandidate]: ...

    async def record_image_pruned(self, build_id: str) -> None: ...

    async def finalize_endpoint_deletions(self) -> bool: ...


class EndpointDockerAdapter(Protocol):
    async def resolve_network(self, selector: str, compose_project: str) -> str: ...

    async def list_containers(self) -> ContainerObservation: ...

    async def start_container(self, spec: ContainerLaunchSpec) -> ObservedContainer: ...

    async def stop_remove_container(self, container_id: str) -> str: ...

    async def prune_image(self, candidate: ImagePruneCandidate) -> None: ...


class EndpointContainerReconciler:
    def __init__(
        self,
        *,
        store: EndpointContainerStore,
        docker: EndpointDockerAdapter,
        deployment_id: str,
        compose_project: str,
        network_selector: str,
        label_namespace: str,
        runtime_environment: Mapping[str, str],
        readiness_timeout_seconds: float,
        drain_timeout_seconds: float,
        reconcile_interval_seconds: float,
        image_retention_count: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._docker = docker
        self._deployment_id = deployment_id
        self._compose_project = compose_project
        self._network_selector = network_selector
        self._label_namespace = label_namespace.rstrip(".")
        self._runtime_environment = {
            str(key): str(value)
            for key, value in runtime_environment.items()
            if str(value)
        }
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._drain_timeout_seconds = drain_timeout_seconds
        self._reconcile_interval_seconds = reconcile_interval_seconds
        self._image_retention_count = max(2, int(image_retention_count))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stopping = asyncio.Event()
        self._leader = False

    @property
    def is_leader(self) -> bool:
        return self._leader

    def request_stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        try:
            while not self._stopping.is_set():
                try:
                    did_work = await self.run_cycle()
                except asyncio.CancelledError:
                    self.request_stop()
                    raise
                except Exception:
                    logger.exception("endpoint container reconciliation cycle failed")
                    self._leader = False
                    await self._store.disconnect()
                    did_work = False
                if not did_work:
                    await self._wait_for_stop(self._reconcile_interval_seconds)
        finally:
            if self._leader:
                with suppress(Exception):
                    await self._store.release_leadership()
            self._leader = False
            await self._store.disconnect()

    async def run_cycle(self) -> bool:
        if self._stopping.is_set():
            return False
        if not self._leader:
            self._leader = await self._store.try_acquire_leadership()
            if not self._leader:
                return False
            logger.info("endpoint container reconciler leadership acquired")

        now = self._clock()
        network_id = await self._docker.resolve_network(
            self._network_selector, self._compose_project
        )
        await self._store.reconcile_deployment_targets()
        intents = sorted(
            await self._store.list_deployment_intents(),
            key=lambda item: (item.endpoint_id, item.rollout_generation),
        )
        registry = await self._store.list_registry_snapshots()
        records = await self._store.list_container_records()
        observation = await self._docker.list_containers()
        observed = list(observation.containers)
        owned = self._owned_containers(observed)
        intents_by_endpoint = {intent.endpoint_id: intent for intent in intents}

        for container, identity in owned:
            intent = intents_by_endpoint.get(identity.endpoint_id)
            if intent is not None and self._identity_matches_intent(identity, intent):
                await self._store.observe_container(identity, container, now=now)

        if observation.complete:
            present_owned_ids = {container.container_id for container, _ in owned}
            missing_record_ids = sorted(
                container_id
                for container_id, record in records.items()
                if record.lifecycle != "removed"
                and container_id not in present_owned_ids
            )
            if missing_record_ids:
                await self._store.record_container_removed(
                    missing_record_ids[0],
                    now=now,
                    timed_out=False,
                    reason="container absent during complete Docker observation",
                    logs="",
                )
                return True

        for container, identity in owned:
            if identity.endpoint_id not in intents_by_endpoint:
                return await self._drain_and_remove(
                    container,
                    identity,
                    registry,
                    records,
                    now=now,
                    reason="endpoint deployment intent was deleted",
                )

        all_converged = True
        for intent in intents:
            acted, converged = await self._reconcile_intent(
                intent,
                owned,
                registry,
                records,
                network_id=network_id,
                now=now,
            )
            all_converged = all_converged and converged
            if acted:
                return True

        if await self._store.finalize_endpoint_deletions():
            return True
        if all_converged:
            for candidate in await self._store.list_image_prune_candidates(
                self._image_retention_count
            ):
                await self._docker.prune_image(candidate)
                await self._store.record_image_pruned(candidate.build_id)
                return True
        return False

    async def _reconcile_intent(
        self,
        intent: DeploymentIntent,
        owned: list[tuple[ObservedContainer, ContainerIdentity]],
        registry: Mapping[str, RegistrySnapshot],
        records: Mapping[str, ContainerRecord],
        *,
        network_id: str,
        now: datetime,
    ) -> tuple[bool, bool]:
        artifact = intent.desired_artifact()
        if artifact is None:
            if intent.phase != "failed":
                await self._store.fail_or_rollback_deployment(
                    intent.deployment_id,
                    "deployment intent does not reference a ready immutable image",
                )
                return True, False
            return False, False
        build_id, revision_id, image_id = artifact
        endpoint_containers = [
            pair for pair in owned if pair[1].endpoint_id == intent.endpoint_id
        ]
        canonical_ids: set[str] = set()

        for slot in range(intent.desired_replica_count):
            matching = [
                pair
                for pair in endpoint_containers
                if self._identity_matches_intent(pair[1], intent)
                and pair[1].slot == slot
                and pair[1].build_id == build_id
                and pair[1].revision_id == revision_id
                and pair[1].image_id == image_id
            ]
            target = self._choose_canonical(matching, registry)
            if target is None:
                if self._rollout_timed_out(intent, now):
                    reason = self._bounded_reason(
                        f"endpoint {intent.endpoint_id} replacement attempts did not produce "
                        f"a running container before the readiness timeout"
                    )
                    await self._store.fail_or_rollback_deployment(
                        intent.deployment_id, reason
                    )
                    return True, False
                try:
                    launched = await self._docker.start_container(
                        self._launch_spec(
                            intent,
                            slot=slot,
                            build_id=build_id,
                            revision_id=revision_id,
                            image_id=image_id,
                            network_id=network_id,
                        )
                    )
                except Exception as exc:
                    reason = self._bounded_reason(
                        f"failed to start endpoint {intent.endpoint_id} slot {slot}: {exc}"
                    )
                    await self._store.fail_or_rollback_deployment(
                        intent.deployment_id, reason
                    )
                    return True, False
                identity = self._require_owned_identity(launched)
                valid_launch = bool(
                    identity is not None
                    and self._identity_matches_intent(identity, intent)
                    and identity.slot == slot
                    and identity.build_id == build_id
                    and identity.revision_id == revision_id
                    and identity.image_id == image_id
                )
                if not valid_launch:
                    cleanup_error = None
                    try:
                        await self._docker.stop_remove_container(launched.container_id)
                    except Exception as exc:
                        cleanup_error = exc
                    reason = "Docker returned a replacement container without the exact required identity"
                    if cleanup_error is not None:
                        reason += f"; cleanup failed: {cleanup_error}"
                    await self._store.fail_or_rollback_deployment(
                        intent.deployment_id, self._bounded_reason(reason)
                    )
                    return True, False
                await self._store.record_container_started(identity, launched, now=now)
                if intent.phase == "pending":
                    await self._store.mark_deployment_rolling(intent.deployment_id)
                return True, False

            target_container, target_identity = target
            canonical_ids.add(target_container.container_id)
            if not target_container.running:
                logs = await self._docker.stop_remove_container(
                    target_container.container_id
                )
                await self._store.record_container_removed(
                    target_container.container_id,
                    now=now,
                    timed_out=False,
                    reason="replacement container was not running",
                    logs=logs,
                )
                if self._rollout_timed_out(intent, now):
                    await self._store.fail_or_rollback_deployment(
                        intent.deployment_id,
                        self._bounded_reason(
                            f"endpoint {intent.endpoint_id} replacement repeatedly stopped "
                            "before the readiness timeout"
                        ),
                    )
                return True, False

            target_snapshot = registry.get(target_identity.worker_id)
            if target_snapshot is None or not target_snapshot.ready_for(
                target_identity
            ):
                started_at = self._container_started_at(
                    target_container, records.get(target_container.container_id)
                )
                if (
                    now - started_at
                ).total_seconds() >= self._readiness_timeout_seconds:
                    reason = self._bounded_reason(
                        f"endpoint {intent.endpoint_id} slot {slot} replacement did not become ready "
                        f"for build {build_id} revision {revision_id} before the readiness timeout"
                    )
                    await self._store.fail_or_rollback_deployment(
                        intent.deployment_id, reason
                    )
                    return True, False
                return False, False

            obsolete = [
                pair
                for pair in endpoint_containers
                if pair[1].slot == slot
                and pair[0].container_id != target_container.container_id
            ]
            if obsolete and not intent.is_rollout:
                obsolete.sort(
                    key=lambda pair: (pair[0].created_at, pair[0].container_id)
                )
                acted = await self._drain_and_remove(
                    obsolete[0][0],
                    obsolete[0][1],
                    registry,
                    records,
                    now=now,
                    reason="slot replacement became ready",
                )
                return acted, False

        if intent.is_rollout:
            await self._store.complete_deployment(
                intent.deployment_id, rolled_back=intent.phase == "rollback"
            )
            return True, True

        excess = [
            pair
            for pair in endpoint_containers
            if pair[1].slot >= intent.desired_replica_count
            or pair[0].container_id not in canonical_ids
        ]
        if excess:
            excess.sort(
                key=lambda pair: (
                    -pair[1].slot,
                    pair[0].created_at,
                    pair[0].container_id,
                )
            )
            acted = await self._drain_and_remove(
                excess[0][0],
                excess[0][1],
                registry,
                records,
                now=now,
                reason="container is outside current endpoint slot intent",
            )
            return acted, False

        return False, True

    async def _drain_and_remove(
        self,
        container: ObservedContainer,
        identity: ContainerIdentity,
        registry: Mapping[str, RegistrySnapshot],
        records: Mapping[str, ContainerRecord],
        *,
        now: datetime,
        reason: str,
    ) -> bool:
        record = records.get(container.container_id)
        if record is None:
            await self._store.observe_container(identity, container, now=now)
        if record is None or record.lifecycle != "draining":
            await self._store.block_worker_claims(identity.worker_id)
            await self._store.mark_container_draining(container.container_id, now=now)
            return True

        snapshot = registry.get(identity.worker_id)
        drain_started_at = record.drain_started_at or now
        timed_out = (
            now - drain_started_at
        ).total_seconds() >= self._drain_timeout_seconds
        if snapshot is not None and snapshot.active and not timed_out:
            return False

        logs = await self._docker.stop_remove_container(container.container_id)
        outcome = reason
        if timed_out and snapshot is not None and snapshot.active:
            outcome = (
                f"{reason}; active task drain timed out and container was force-stopped"
            )
        await self._store.record_container_removed(
            container.container_id,
            now=now,
            timed_out=bool(timed_out and snapshot is not None and snapshot.active),
            reason=self._bounded_reason(outcome),
            logs=logs,
        )
        return True

    def _owned_containers(
        self, observed: list[ObservedContainer]
    ) -> list[tuple[ObservedContainer, ContainerIdentity]]:
        result: list[tuple[ObservedContainer, ContainerIdentity]] = []
        for container in observed:
            identity = self._require_owned_identity(container)
            if identity is not None:
                result.append((container, identity))
        return result

    def _require_owned_identity(
        self, container: ObservedContainer
    ) -> ContainerIdentity | None:
        labels = {str(key): str(value) for key, value in container.labels.items()}
        prefix = self._label_namespace
        keys = {
            "platform_owner": f"{prefix}.platform-owner",
            "owner": f"{prefix}.owner",
            "kind": f"{prefix}.managed-kind",
            "project": f"{prefix}.compose-project",
            "endpoint": f"{prefix}.endpoint-id",
            "deployment": f"{prefix}.deployment-id",
            "slot": f"{prefix}.slot",
            "build": f"{prefix}.build-id",
            "revision": f"{prefix}.revision-id",
            "generation": f"{prefix}.rollout-generation",
            "worker": f"{prefix}.worker-id",
            "image": f"{prefix}.image-id",
        }
        if any(not labels.get(key, "").strip() for key in keys.values()):
            return None
        if (
            labels[keys["platform_owner"]] != PLATFORM_OWNER
            or labels[keys["owner"]] != self._deployment_id
            or labels[keys["kind"]] != MANAGED_CONTAINER_KIND
            or labels[keys["project"]] != self._compose_project
            or labels[keys["image"]] != container.image_id
        ):
            return None
        try:
            slot = int(labels[keys["slot"]])
            generation = int(labels[keys["generation"]])
        except (TypeError, ValueError):
            return None
        if slot < 0 or generation < 0:
            return None
        return ContainerIdentity(
            owner=labels[keys["owner"]],
            compose_project=labels[keys["project"]],
            endpoint_id=labels[keys["endpoint"]],
            deployment_id=labels[keys["deployment"]],
            slot=slot,
            build_id=labels[keys["build"]],
            revision_id=labels[keys["revision"]],
            rollout_generation=generation,
            worker_id=labels[keys["worker"]],
            image_id=labels[keys["image"]],
        )

    def _launch_spec(
        self,
        intent: DeploymentIntent,
        *,
        slot: int,
        build_id: str,
        revision_id: str,
        image_id: str,
        network_id: str,
    ) -> ContainerLaunchSpec:
        name = managed_container_name(
            deployment_owner=self._deployment_id,
            endpoint_id=intent.endpoint_id,
            slot=slot,
            build_id=build_id,
            rollout_generation=intent.rollout_generation,
        )
        worker_id = name
        prefix = self._label_namespace
        labels = {
            f"{prefix}.platform-owner": PLATFORM_OWNER,
            f"{prefix}.owner": self._deployment_id,
            f"{prefix}.managed-kind": MANAGED_CONTAINER_KIND,
            f"{prefix}.compose-project": self._compose_project,
            f"{prefix}.endpoint-id": intent.endpoint_id,
            f"{prefix}.deployment-id": intent.deployment_id,
            f"{prefix}.slot": str(slot),
            f"{prefix}.build-id": build_id,
            f"{prefix}.revision-id": revision_id,
            f"{prefix}.rollout-generation": str(intent.rollout_generation),
            f"{prefix}.worker-id": worker_id,
            f"{prefix}.image-id": image_id,
        }
        environment = {
            **self._runtime_environment,
            "DSPY_TRAINER_ENDPOINT_WORKER_MODE": "managed_image",
            "DSPY_TRAINER_ENDPOINT_ID": intent.endpoint_id,
            "DSPY_TRAINER_WORKER_ID": worker_id,
            "DSPY_TRAINER_ENDPOINT_DEPLOYMENT_ID": intent.deployment_id,
            "DSPY_TRAINER_ENDPOINT_SLOT": str(slot),
            "DSPY_TRAINER_ENDPOINT_ROLLOUT_GENERATION": str(intent.rollout_generation),
            "DSPY_TRAINER_DEPLOYMENT_ID": self._deployment_id,
        }
        return ContainerLaunchSpec(
            name=name,
            worker_id=worker_id,
            image_id=image_id,
            module_id=intent.module_id,
            endpoint_id=intent.endpoint_id,
            deployment_id=intent.deployment_id,
            build_id=build_id,
            revision_id=revision_id,
            slot=slot,
            rollout_generation=intent.rollout_generation,
            network_id=network_id,
            labels=labels,
            environment=environment,
        )

    @staticmethod
    def _identity_matches_intent(
        identity: ContainerIdentity, intent: DeploymentIntent
    ) -> bool:
        return bool(
            identity.endpoint_id == intent.endpoint_id
            and identity.deployment_id == intent.deployment_id
            and identity.rollout_generation == intent.rollout_generation
        )

    def _rollout_timed_out(self, intent: DeploymentIntent, now: datetime) -> bool:
        return bool(
            intent.phase in {"rolling", "draining"}
            and intent.rollout_started_at is not None
            and (now - intent.rollout_started_at).total_seconds()
            >= self._readiness_timeout_seconds
        )

    @staticmethod
    def _choose_canonical(
        matching: list[tuple[ObservedContainer, ContainerIdentity]],
        registry: Mapping[str, RegistrySnapshot],
    ) -> tuple[ObservedContainer, ContainerIdentity] | None:
        if not matching:
            return None
        return sorted(
            matching,
            key=lambda pair: (
                (
                    0
                    if registry.get(pair[1].worker_id)
                    and registry[pair[1].worker_id].ready_for(pair[1])
                    else 1
                ),
                0 if pair[0].running else 1,
                pair[0].created_at,
                pair[0].container_id,
            ),
        )[0]

    @staticmethod
    def _container_started_at(
        container: ObservedContainer, record: ContainerRecord | None
    ) -> datetime:
        return (
            record.started_at if record and record.started_at else container.created_at
        )

    @staticmethod
    def _bounded_reason(reason: str) -> str:
        return str(reason).strip()[:MAX_FAILURE_REASON_CHARS]

    async def _wait_for_stop(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=timeout)
        except TimeoutError:
            return


class PostgresEndpointContainerStore:
    def __init__(
        self,
        *,
        postgres_dsn: str,
        instance_id: str,
        leader_timeout_seconds: float,
    ) -> None:
        self._postgres_dsn = postgres_dsn
        self._instance_id = instance_id
        self._leader_timeout_seconds = leader_timeout_seconds
        self._connection: Any | None = None
        self._leader = False

    async def _conn(self) -> Any:
        if self._connection is not None and self._connection.is_closed():
            self._connection = None
            if self._leader:
                self._leader = False
                raise ConnectionError(
                    "Postgres session closed while holding endpoint reconciler leadership"
                )
        if self._connection is None:
            import asyncpg

            self._connection = await asyncpg.connect(
                self._postgres_dsn,
                command_timeout=self._leader_timeout_seconds,
                server_settings={
                    "application_name": f"dspy-trainer-endpoint-reconciler:{self._instance_id}"
                },
            )
            self._leader = False
        return self._connection

    async def try_acquire_leadership(self) -> bool:
        conn = await self._conn()
        self._leader = bool(
            await conn.fetchval(
                "select pg_try_advisory_lock($1)", _RECONCILER_ADVISORY_LOCK
            )
        )
        return self._leader

    async def release_leadership(self) -> None:
        if self._connection is None or self._connection.is_closed() or not self._leader:
            self._leader = False
            return
        await self._connection.fetchval(
            "select pg_advisory_unlock($1)", _RECONCILER_ADVISORY_LOCK
        )
        self._leader = False

    async def disconnect(self) -> None:
        if self._connection is not None and not self._connection.is_closed():
            await self._connection.close()
        self._connection = None
        self._leader = False

    async def reconcile_deployment_targets(self) -> int:
        conn = await self._conn()
        async with conn.transaction():
            candidate = await conn.fetchrow("""
                with latest as (
                  select distinct on (d.endpoint_id) d.*
                  from endpoint_deployments d
                  order by d.endpoint_id, d.rollout_generation desc
                )
                select e.id as endpoint_id, greatest(1, e.pinned_worker_count) as replica_count,
                       d.*, ready.id as ready_build_id,
                       m.current_revision_id as ready_revision_id,
                       m.id as ready_module_import_id
                from bundle_endpoints e
                join latest d on d.endpoint_id = e.id
                join module_imports m on m.id = coalesce(d.failed_module_import_id, e.module_import_id)
                join runtime_bundles rb on rb.module_import_id = m.id
                join lateral (
                  select b.id
                  from revision_image_builds b
                  where b.revision_id = m.current_revision_id
                    and b.status = 'ready'
                    and b.image_id is not null
                  order by b.generation desc
                  limit 1
                ) ready on true
                where e.delete_requested_at is null
                  and m.deleted_at is null
                  and rb.validation_status = 'passed'
                  and rb.validation_revision_id = m.current_revision_id
                  and d.phase in ('legacy_static', 'ready', 'failed')
                  and ready.id is distinct from d.active_build_id
                  and ready.id is distinct from d.target_build_id
                  and ready.id is distinct from d.failed_build_id
                order by e.created_at, e.id
                limit 1
                for update of e
                """)
            if candidate is None:
                return 0
            phase = str(candidate["phase"] or "")
            legacy_fallback = bool(candidate["legacy_fallback"])
            if legacy_fallback or phase == "failed":
                await conn.execute(
                    """
                    update endpoint_deployments
                    set target_build_id = $2, target_revision_id = $3,
                        target_module_import_id = $4,
                        phase = 'pending', desired_replica_count = $5,
                        rollout_generation = rollout_generation + case
                          when target_build_id is null and phase = 'legacy_static' then 0 else 1 end,
                        failed_build_id = null, failed_revision_id = null,
                        failed_module_import_id = null,
                        rollout_started_at = now(), ready_at = null, deadline_at = null,
                        failure_reason = null, updated_at = now()
                    where id = $1
                    """,
                    str(candidate["id"]),
                    str(candidate["ready_build_id"]),
                    str(candidate["ready_revision_id"]),
                    str(candidate["ready_module_import_id"]),
                    int(candidate["replica_count"]),
                )
                return 1
            next_generation = int(candidate["rollout_generation"] or 0) + 1
            endpoint_id = str(candidate["endpoint_id"])
            await conn.execute(
                """
                insert into endpoint_deployments (
                  id, endpoint_id, active_build_id, active_revision_id, active_module_import_id,
                  target_build_id, target_revision_id, target_module_import_id,
                  previous_build_id, previous_revision_id, previous_module_import_id,
                  phase, desired_replica_count, legacy_fallback, rollout_generation,
                  rollout_started_at, created_at, updated_at
                ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                          'pending', $12, false, $13, now(), now(), now())
                """,
                f"rollout-{endpoint_id}-{next_generation}-{uuid4().hex[:12]}",
                endpoint_id,
                _text(candidate["active_build_id"]),
                _text(candidate["active_revision_id"]),
                _text(candidate["active_module_import_id"]),
                str(candidate["ready_build_id"]),
                str(candidate["ready_revision_id"]),
                str(candidate["ready_module_import_id"]),
                _text(candidate["previous_build_id"]),
                _text(candidate["previous_revision_id"]),
                _text(candidate["previous_module_import_id"]),
                int(candidate["replica_count"]),
                next_generation,
            )
            return 1

    async def list_deployment_intents(self) -> list[DeploymentIntent]:
        conn = await self._conn()
        rows = await conn.fetch("""
            with latest as (
              select distinct on (d.endpoint_id)
                     d.*, e.module_import_id as endpoint_module_import_id
              from endpoint_deployments d
              join bundle_endpoints e on e.id = d.endpoint_id
              where e.delete_requested_at is null
              order by d.endpoint_id, d.rollout_generation desc
            )
            select latest.*,
                   active.image_id as active_image_id,
                   target.image_id as target_image_id,
                   previous.image_id as previous_image_id
            from latest
            left join revision_image_builds active on active.id = latest.active_build_id
            left join revision_image_builds target on target.id = latest.target_build_id
            left join revision_image_builds previous on previous.id = latest.previous_build_id
            where latest.phase <> 'legacy_static'
            order by latest.endpoint_id
            """)
        return [
            DeploymentIntent(
                deployment_id=str(row["id"]),
                endpoint_id=str(row["endpoint_id"]),
                module_id=str(
                    row["target_module_import_id"]
                    or row["active_module_import_id"]
                    or row["endpoint_module_import_id"]
                ),
                phase=str(row["phase"]),
                desired_replica_count=int(row["desired_replica_count"]),
                rollout_generation=int(row["rollout_generation"]),
                active_build_id=_text(row["active_build_id"]),
                active_revision_id=_text(row["active_revision_id"]),
                active_image_id=_text(row["active_image_id"]),
                target_build_id=_text(row["target_build_id"]),
                target_revision_id=_text(row["target_revision_id"]),
                target_image_id=_text(row["target_image_id"]),
                previous_build_id=_text(row["previous_build_id"]),
                previous_revision_id=_text(row["previous_revision_id"]),
                previous_image_id=_text(row["previous_image_id"]),
                rollout_started_at=row["rollout_started_at"],
            )
            for row in rows
        ]

    async def list_registry_snapshots(self) -> dict[str, RegistrySnapshot]:
        conn = await self._conn()
        rows = await conn.fetch("""
            select worker_id, status, assigned_endpoint_id, task_id, last_seen_at,
                   heartbeat_expires_at, runtime_metadata
            from endpoint_worker_registrations
            """)
        now = datetime.now(timezone.utc)
        snapshots: dict[str, RegistrySnapshot] = {}
        for row in rows:
            metadata = _metadata(row["runtime_metadata"])
            desired_build_id = _text(metadata.get("desired_build_id"))
            warmed_build_id = _text(metadata.get("warmed_build_id"))
            desired_revision_id = _text(metadata.get("desired_revision_id"))
            warmed_revision_id = _text(metadata.get("warmed_revision_id"))
            snapshots[str(row["worker_id"])] = RegistrySnapshot(
                worker_id=str(row["worker_id"]),
                status=str(row["status"] or "unknown"),
                assigned_endpoint_id=_text(row["assigned_endpoint_id"]),
                reported_endpoint_id=_text(metadata.get("endpoint_id")),
                execution_mode=_text(metadata.get("execution_mode")),
                build_id=(
                    warmed_build_id if desired_build_id == warmed_build_id else None
                ),
                revision_id=(
                    warmed_revision_id
                    if desired_revision_id == warmed_revision_id
                    else None
                ),
                deployment_id=_text(metadata.get("endpoint_deployment_id")),
                slot=_integer(metadata.get("endpoint_slot")),
                rollout_generation=_integer(
                    metadata.get("endpoint_rollout_generation")
                ),
                task_id=_text(row["task_id"]),
                is_live=bool(
                    row["heartbeat_expires_at"] and row["heartbeat_expires_at"] > now
                ),
                last_seen_at=row["last_seen_at"],
            )
        return snapshots

    async def list_container_records(self) -> dict[str, ContainerRecord]:
        conn = await self._conn()
        rows = await conn.fetch("""
            select container_id, lifecycle, started_at, drain_started_at
            from managed_endpoint_containers
            where lifecycle <> 'removed'
            """)
        return {
            str(row["container_id"]): ContainerRecord(
                container_id=str(row["container_id"]),
                lifecycle=str(row["lifecycle"]),
                started_at=row["started_at"],
                drain_started_at=row["drain_started_at"],
            )
            for row in rows
        }

    async def observe_container(
        self,
        identity: ContainerIdentity,
        container: ObservedContainer,
        *,
        now: datetime,
    ) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            insert into managed_endpoint_containers (
              container_id, container_name, endpoint_id, deployment_id, build_id, revision_id,
              slot, worker_id, lifecycle, last_observed_at, started_at, created_at, updated_at
            ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11, $10)
            on conflict (container_id) do update set
              container_name = excluded.container_name,
              worker_id = excluded.worker_id,
              last_observed_at = excluded.last_observed_at,
              lifecycle = case
                when managed_endpoint_containers.lifecycle in ('draining', 'failed') then managed_endpoint_containers.lifecycle
                when managed_endpoint_containers.lifecycle = 'removed' then 'removed'
                else excluded.lifecycle
              end,
              updated_at = excluded.updated_at
            """,
            container.container_id,
            container.name,
            identity.endpoint_id,
            identity.deployment_id,
            identity.build_id,
            identity.revision_id,
            identity.slot,
            identity.worker_id,
            "ready" if container.running else "stopped",
            now,
            container.created_at,
        )

    async def record_container_started(
        self,
        identity: ContainerIdentity,
        container: ObservedContainer,
        *,
        now: datetime,
    ) -> None:
        await self.observe_container(identity, container, now=now)

    async def mark_container_draining(
        self, container_id: str, *, now: datetime
    ) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            update managed_endpoint_containers
            set lifecycle = 'draining', drain_started_at = coalesce(drain_started_at, $2), updated_at = $2
            where container_id = $1 and lifecycle <> 'removed'
            """,
            container_id,
            now,
        )

    async def record_container_removed(
        self,
        container_id: str,
        *,
        now: datetime,
        timed_out: bool,
        reason: str | None,
        logs: str,
    ) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            update managed_endpoint_containers
            set lifecycle = 'removed', stopped_at = $2, drain_timed_out = $3,
                failure_reason = $4, container_log = $5, updated_at = $2
            where container_id = $1
            """,
            container_id,
            now,
            timed_out,
            _bounded_text(reason, MAX_FAILURE_REASON_CHARS),
            _bounded_utf8(logs, _CONTAINER_LOG_MAX_BYTES),
        )

    async def block_worker_claims(self, worker_id: str) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            with blocked as (
              update managed_endpoint_containers
              set lifecycle = 'draining', drain_started_at = coalesce(drain_started_at, now()),
                  updated_at = now()
              where worker_id = $1 and lifecycle in ('created', 'starting', 'ready', 'busy')
              returning container_id
            )
            update endpoint_worker_registrations
            set assigned_endpoint_id = null, updated_at = now()
            where worker_id = $1
            """,
            worker_id,
        )

    async def mark_deployment_rolling(self, deployment_id: str) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            update endpoint_deployments
            set phase = case when phase = 'pending' then 'rolling' else phase end,
                rollout_started_at = coalesce(rollout_started_at, now()), updated_at = now()
            where id = $1
            """,
            deployment_id,
        )

    async def fail_or_rollback_deployment(
        self, deployment_id: str, reason: str
    ) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            update endpoint_deployments
            set failed_build_id = target_build_id,
                failed_revision_id = target_revision_id,
                failed_module_import_id = target_module_import_id,
                phase = case when active_build_id is null then 'failed' else 'rollback' end,
                target_build_id = active_build_id,
                target_revision_id = active_revision_id,
                target_module_import_id = active_module_import_id,
                failure_reason = $2,
                deadline_at = null,
                updated_at = now()
            where id = $1
            """,
            deployment_id,
            _bounded_text(reason, MAX_FAILURE_REASON_CHARS),
        )

    async def complete_deployment(
        self, deployment_id: str, *, rolled_back: bool
    ) -> None:
        conn = await self._conn()
        if rolled_back:
            await conn.execute(
                """
                update endpoint_deployments
                set target_build_id = null, target_revision_id = null,
                    target_module_import_id = null, phase = 'ready',
                    deadline_at = null, ready_at = now(), updated_at = now()
                where id = $1
                """,
                deployment_id,
            )
            return
        await conn.execute(
            """
            with promoted as (
              update endpoint_deployments
              set previous_build_id = active_build_id,
                  previous_revision_id = active_revision_id,
                  previous_module_import_id = active_module_import_id,
                  active_build_id = target_build_id,
                  active_revision_id = target_revision_id,
                  active_module_import_id = target_module_import_id,
                  target_build_id = null,
                  target_revision_id = null,
                  target_module_import_id = null,
                  phase = 'ready', legacy_fallback = false,
                  deadline_at = null, failure_reason = null,
                  ready_at = now(), updated_at = now()
              where id = $1
              returning endpoint_id, active_module_import_id
            )
            update bundle_endpoints e
            set module_import_id = promoted.active_module_import_id, updated_at = now()
            from promoted
            where e.id = promoted.endpoint_id
            """,
            deployment_id,
        )

    async def list_image_prune_candidates(
        self, retention_count: int
    ) -> list[ImagePruneCandidate]:
        conn = await self._conn()
        rows = await conn.fetch(
            """
            with ranked as (
              select b.id, b.revision_id, r.module_import_id, b.image_id,
                     row_number() over (
                       partition by r.module_import_id
                       order by b.generation desc, b.finished_at desc nulls last, b.created_at desc
                     ) as position
              from revision_image_builds b
              join bundle_revisions r on r.id = b.revision_id
              where b.status = 'ready' and b.image_id is not null
            ), referenced as (
              select active_build_id as build_id from endpoint_deployments where active_build_id is not null
              union select target_build_id from endpoint_deployments where target_build_id is not null
              union select previous_build_id from endpoint_deployments where previous_build_id is not null
              union select failed_build_id from endpoint_deployments where failed_build_id is not null
              union select build_id from managed_endpoint_containers where lifecycle <> 'removed'
            )
            select ranked.id, ranked.revision_id, ranked.module_import_id, ranked.image_id
            from ranked
            where ranked.position > $1
              and not exists (select 1 from referenced where referenced.build_id = ranked.id)
            order by ranked.module_import_id, ranked.position desc
            """,
            max(2, int(retention_count)),
        )
        return [
            ImagePruneCandidate(
                image_id=str(row["image_id"]),
                module_id=str(row["module_import_id"]),
                build_id=str(row["id"]),
                revision_id=str(row["revision_id"]),
            )
            for row in rows
        ]

    async def record_image_pruned(self, build_id: str) -> None:
        conn = await self._conn()
        await conn.execute(
            """
            update revision_image_builds
            set status = 'pruned', updated_at = now()
            where id = $1 and status = 'ready'
            """,
            build_id,
        )

    async def finalize_endpoint_deletions(self) -> bool:
        conn = await self._conn()
        async with conn.transaction():
            row = await conn.fetchrow("""
                select e.id
                from bundle_endpoints e
                where e.delete_requested_at is not null
                  and not exists (
                    select 1
                    from managed_endpoint_containers c
                    where c.endpoint_id = e.id and c.lifecycle <> 'removed'
                  )
                order by e.delete_requested_at, e.id
                limit 1
                for update skip locked
                """)
            if row is None:
                return False
            endpoint_id = str(row["id"])
            await conn.execute(
                "delete from managed_endpoint_containers where endpoint_id = $1",
                endpoint_id,
            )
            await conn.execute(
                "delete from endpoint_deployments where endpoint_id = $1", endpoint_id
            )
            await conn.execute(
                "delete from bundle_endpoints where id = $1 and delete_requested_at is not null",
                endpoint_id,
            )
        return True


class DockerSdkEndpointAdapter:
    def __init__(
        self,
        *,
        deployment_id: str,
        compose_project: str,
        label_namespace: str,
    ) -> None:
        self._deployment_id = deployment_id
        self._compose_project = compose_project
        self._label_namespace = label_namespace.rstrip(".")
        self._client: Any | None = None

    def _docker(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def resolve_network(self, selector: str, compose_project: str) -> str:
        def resolve() -> str:
            matches = self._docker().networks.list(names=[selector])
            exact = []
            for network in matches:
                attrs = dict(network.attrs or {})
                labels = dict(attrs.get("Labels") or {})
                name = str(attrs.get("Name") or getattr(network, "name", ""))
                if (
                    name == selector
                    and labels.get("com.docker.compose.project") == compose_project
                ):
                    exact.append(network)
            if len(exact) != 1:
                raise RuntimeError(
                    f"expected exactly one Compose network named {selector!r} with project label {compose_project!r}; found {len(exact)}"
                )
            return str(exact[0].id)

        return await asyncio.to_thread(resolve)

    async def list_containers(self) -> ContainerObservation:
        def collect() -> ContainerObservation:
            prefix = self._label_namespace
            containers = self._docker().containers.list(
                all=True,
                filters={
                    "label": [
                        f"{prefix}.owner={self._deployment_id}",
                        f"{prefix}.managed-kind={MANAGED_CONTAINER_KIND}",
                    ]
                },
            )
            observed = tuple(self._observed(container) for container in containers)
            return ContainerObservation(containers=observed, complete=True)

        return await asyncio.to_thread(collect)

    async def start_container(self, spec: ContainerLaunchSpec) -> ObservedContainer:
        def start() -> ObservedContainer:
            client = self._docker()
            image = client.images.get(spec.image_id)
            inspection = type(
                "Inspection",
                (),
                {
                    "labels": dict(
                        ((image.attrs or {}).get("Config") or {}).get("Labels") or {}
                    )
                },
            )()
            labels = require_managed_revision_image(
                inspection, owner=self._deployment_id
            )
            mismatches = {
                LABEL_MODULE_ID: spec.module_id,
                LABEL_BUILD_ID: spec.build_id,
                LABEL_REVISION_ID: spec.revision_id,
            }
            wrong = [
                key
                for key, expected in mismatches.items()
                if labels.get(key) != expected
            ]
            if wrong or str(image.id) != spec.image_id:
                raise RuntimeError(
                    "managed endpoint image identity does not match deployment intent"
                )
            container = client.containers.run(
                spec.image_id,
                name=spec.name,
                detach=True,
                environment=dict(spec.environment),
                labels=dict(spec.labels),
                network=spec.network_id,
                restart_policy={"Name": "unless-stopped"},
            )
            container.reload()
            observed = self._observed(container)
            if observed.image_id != spec.image_id:
                with suppress(Exception):
                    container.stop(timeout=1)
                    container.remove(force=True)
                raise RuntimeError(
                    "started container does not use the requested image ID"
                )
            return observed

        return await asyncio.to_thread(start)

    async def stop_remove_container(self, container_id: str) -> str:
        def stop_remove() -> str:
            container = self._docker().containers.get(container_id)
            raw_logs = container.logs(stdout=True, stderr=True, tail=2000) or b""
            if isinstance(raw_logs, bytes):
                logs = raw_logs.decode("utf-8", errors="replace")
            else:
                logs = str(raw_logs)
            container.stop(timeout=10)
            container.remove(v=True)
            return _bounded_utf8(logs, _CONTAINER_LOG_MAX_BYTES)

        return await asyncio.to_thread(stop_remove)

    async def prune_image(self, candidate: ImagePruneCandidate) -> None:
        def prune() -> None:
            client = self._docker()
            image = client.images.get(candidate.image_id)
            inspection = type(
                "Inspection",
                (),
                {
                    "labels": dict(
                        ((image.attrs or {}).get("Config") or {}).get("Labels") or {}
                    )
                },
            )()
            labels = require_managed_revision_image(
                inspection, owner=self._deployment_id
            )
            if (
                str(image.id) != candidate.image_id
                or labels.get(LABEL_MODULE_ID) != candidate.module_id
                or labels.get(LABEL_BUILD_ID) != candidate.build_id
                or labels.get(LABEL_REVISION_ID) != candidate.revision_id
            ):
                raise RuntimeError(
                    "refusing to prune image with mismatched ownership labels"
                )
            client.images.remove(candidate.image_id, force=False, noprune=False)

        await asyncio.to_thread(prune)

    @staticmethod
    def _observed(container: Any) -> ObservedContainer:
        container.reload()
        attrs = dict(container.attrs or {})
        config = dict(attrs.get("Config") or {})
        state = dict(attrs.get("State") or {})
        created = _parse_datetime(attrs.get("Created"))
        return ObservedContainer(
            container_id=str(attrs.get("Id") or container.id),
            name=str(attrs.get("Name") or getattr(container, "name", "")).lstrip("/"),
            image_id=str(attrs.get("Image") or ""),
            labels={
                str(key): str(value)
                for key, value in dict(config.get("Labels") or {}).items()
            },
            state=str(state.get("Status") or getattr(container, "status", "unknown")),
            created_at=created,
        )


def managed_container_name(
    *,
    deployment_owner: str,
    endpoint_id: str,
    slot: int,
    build_id: str,
    rollout_generation: int,
) -> str:
    endpoint_slug = re.sub(r"[^a-z0-9]+", "-", endpoint_id.lower()).strip("-")[:12]
    endpoint_slug = endpoint_slug or "endpoint"
    digest = hashlib.sha256(
        f"{deployment_owner}\0{endpoint_id}\0{slot}\0{build_id}\0{rollout_generation}".encode()
    ).hexdigest()[:20]
    return f"dspy-ep-{endpoint_slug}-{slot}-{digest}"[:63]


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _integer(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    normalized = str(value or "").strip().replace("Z", "+00:00")
    if normalized:
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _bounded_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized[:limit] or None


def _bounded_utf8(value: str, max_bytes: int) -> str:
    encoded = str(value).encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8", errors="replace")
    return encoded[-max_bytes:].decode("utf-8", errors="ignore")
