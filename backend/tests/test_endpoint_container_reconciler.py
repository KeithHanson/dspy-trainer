import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.endpoint_container_reconciler import (
    ContainerRecord,
    DeploymentIntent,
    DockerSdkEndpointAdapter,
    EndpointContainerReconciler,
    ImagePruneCandidate,
    ObservedContainer,
    RegistrySnapshot,
    managed_container_name,
)
from endpoint_worker import claim_or_requeue_endpoint_job

NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)
OWNER = "deployment-owner"
PROJECT = "compose-project"
NAMESPACE = "io.dspy-trainer"


class _Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class _Store:
    def __init__(self, intents):
        self.intents = list(intents)
        self.registry = {}
        self.records = {}
        self.events = []
        self.prune_candidates = []
        self.leader = False

    async def try_acquire_leadership(self):
        self.leader = True
        return True

    async def release_leadership(self):
        self.leader = False

    async def disconnect(self):
        return None

    async def list_deployment_intents(self):
        return list(self.intents)

    async def list_registry_snapshots(self):
        return dict(self.registry)

    async def list_container_records(self):
        return dict(self.records)

    async def observe_container(self, identity, container, *, now):
        self.records.setdefault(
            container.container_id,
            ContainerRecord(
                container_id=container.container_id,
                lifecycle="ready" if container.running else "stopped",
                started_at=container.created_at,
            ),
        )

    async def record_container_started(self, identity, container, *, now):
        self.records[container.container_id] = ContainerRecord(
            container_id=container.container_id,
            lifecycle="starting",
            started_at=now,
        )
        self.events.append(
            ("started", identity.endpoint_id, identity.slot, identity.build_id)
        )

    async def mark_container_draining(self, container_id, *, now):
        current = self.records.get(container_id)
        self.records[container_id] = ContainerRecord(
            container_id=container_id,
            lifecycle="draining",
            started_at=current.started_at if current else now,
            drain_started_at=(current.drain_started_at if current else None) or now,
        )
        self.events.append(("draining", container_id))

    async def record_container_removed(
        self, container_id, *, now, timed_out, reason, logs
    ):
        self.records[container_id] = ContainerRecord(
            container_id=container_id,
            lifecycle="removed",
            started_at=(
                self.records.get(container_id).started_at
                if self.records.get(container_id)
                else None
            ),
            drain_started_at=(
                self.records.get(container_id).drain_started_at
                if self.records.get(container_id)
                else None
            ),
        )
        self.events.append(("removed", container_id, timed_out, reason, logs))

    async def block_worker_claims(self, worker_id):
        self.events.append(("blocked", worker_id))
        snapshot = self.registry.get(worker_id)
        if snapshot is not None:
            self.registry[worker_id] = replace(snapshot, assigned_endpoint_id=None)

    async def mark_deployment_rolling(self, deployment_id):
        self.events.append(("rolling", deployment_id))
        self._replace_intent(deployment_id, phase="rolling")

    async def fail_or_rollback_deployment(self, deployment_id, reason):
        intent = self._intent(deployment_id)
        phase = "rollback" if intent.active_build_id else "failed"
        changes = {"phase": phase}
        if intent.active_build_id:
            changes.update(
                target_build_id=intent.active_build_id,
                target_revision_id=intent.active_revision_id,
                target_image_id=intent.active_image_id,
            )
        self._replace_intent(deployment_id, **changes)
        self.events.append((phase, deployment_id, reason))

    async def complete_deployment(self, deployment_id, *, rolled_back):
        intent = self._intent(deployment_id)
        changes = {
            "phase": "ready",
            "target_build_id": None,
            "target_revision_id": None,
            "target_image_id": None,
        }
        if not rolled_back:
            changes.update(
                previous_build_id=intent.active_build_id,
                previous_revision_id=intent.active_revision_id,
                previous_image_id=intent.active_image_id,
                active_build_id=intent.target_build_id,
                active_revision_id=intent.target_revision_id,
                active_image_id=intent.target_image_id,
            )
        self._replace_intent(deployment_id, **changes)
        self.events.append(("completed", deployment_id, rolled_back))

    async def list_image_prune_candidates(self, retention_count):
        assert retention_count >= 2
        return list(self.prune_candidates)

    async def record_image_pruned(self, build_id):
        self.prune_candidates = [
            candidate
            for candidate in self.prune_candidates
            if candidate.build_id != build_id
        ]
        self.events.append(("pruned", build_id))

    def _intent(self, deployment_id):
        return next(
            item for item in self.intents if item.deployment_id == deployment_id
        )

    def _replace_intent(self, deployment_id, **changes):
        self.intents = [
            replace(item, **changes) if item.deployment_id == deployment_id else item
            for item in self.intents
        ]


class _Docker:
    def __init__(self, clock):
        self.clock = clock
        self.containers = []
        self.events = []
        self.pruned = []
        self.fail_start = False
        self._next_id = 1

    async def resolve_network(self, selector, compose_project):
        assert selector == "runtime-network"
        assert compose_project == PROJECT
        return "network-id"

    async def list_containers(self):
        return list(self.containers)

    async def start_container(self, spec):
        if self.fail_start:
            raise RuntimeError("injected start failure")
        container = ObservedContainer(
            container_id=f"container-{self._next_id}",
            name=spec.name,
            image_id=spec.image_id,
            labels=dict(spec.labels),
            state="running",
            created_at=self.clock(),
        )
        self._next_id += 1
        self.containers.append(container)
        self.events.append(
            (
                "start",
                container.container_id,
                spec.endpoint_id,
                spec.slot,
                spec.build_id,
            )
        )
        return container

    async def stop_remove_container(self, container_id):
        self.events.append(("stop", container_id))
        self.containers = [
            item for item in self.containers if item.container_id != container_id
        ]
        return f"logs for {container_id}"

    async def prune_image(self, candidate):
        self.pruned.append(candidate.image_id)


def _intent(*, count=1, phase="pending", active=True):
    return DeploymentIntent(
        deployment_id="deployment-1",
        endpoint_id="endpoint-1",
        module_id="module-1",
        phase=phase,
        desired_replica_count=count,
        rollout_generation=2,
        active_build_id="build-old" if active else None,
        active_revision_id="revision-old" if active else None,
        active_image_id="sha256:old" if active else None,
        target_build_id="build-new" if phase != "ready" else None,
        target_revision_id="revision-new" if phase != "ready" else None,
        target_image_id="sha256:new" if phase != "ready" else None,
    )


def _labels(
    *,
    endpoint="endpoint-1",
    deployment="deployment-1",
    slot=0,
    build="build-old",
    revision="revision-old",
    image="sha256:old",
    generation=1,
    worker=None,
):
    worker = worker or f"worker-{build}-{slot}-{generation}"
    return {
        f"{NAMESPACE}.platform-owner": "dspy-trainer",
        f"{NAMESPACE}.owner": OWNER,
        f"{NAMESPACE}.managed-kind": "endpoint-worker",
        f"{NAMESPACE}.compose-project": PROJECT,
        f"{NAMESPACE}.endpoint-id": endpoint,
        f"{NAMESPACE}.deployment-id": deployment,
        f"{NAMESPACE}.slot": str(slot),
        f"{NAMESPACE}.build-id": build,
        f"{NAMESPACE}.revision-id": revision,
        f"{NAMESPACE}.rollout-generation": str(generation),
        f"{NAMESPACE}.worker-id": worker,
        f"{NAMESPACE}.image-id": image,
    }


def _container(
    container_id,
    *,
    slot=0,
    build="build-old",
    revision="revision-old",
    image="sha256:old",
    generation=1,
    created_at=NOW,
    labels=None,
):
    effective_labels = labels or _labels(
        slot=slot,
        build=build,
        revision=revision,
        image=image,
        generation=generation,
    )
    return ObservedContainer(
        container_id=container_id,
        name=f"container-name-{container_id}",
        image_id=image,
        labels=effective_labels,
        state="running",
        created_at=created_at,
    )


def _ready(container):
    labels = container.labels
    prefix = NAMESPACE
    return RegistrySnapshot(
        worker_id=labels[f"{prefix}.worker-id"],
        status="listening",
        assigned_endpoint_id=labels[f"{prefix}.endpoint-id"],
        reported_endpoint_id=labels[f"{prefix}.endpoint-id"],
        execution_mode="managed_image",
        build_id=labels[f"{prefix}.build-id"],
        revision_id=labels[f"{prefix}.revision-id"],
        task_id=None,
        is_live=True,
        last_seen_at=NOW,
    )


def _reconciler(store, docker, clock, *, readiness=30, drain=20):
    return EndpointContainerReconciler(
        store=store,
        docker=docker,
        deployment_id=OWNER,
        compose_project=PROJECT,
        network_selector="runtime-network",
        label_namespace=NAMESPACE,
        runtime_environment={"DSPY_TRAINER_REDIS_URL": "redis://redis:6379/0"},
        readiness_timeout_seconds=readiness,
        drain_timeout_seconds=drain,
        reconcile_interval_seconds=0.01,
        image_retention_count=2,
        clock=clock,
    )


def _new_container(docker):
    return next(
        item
        for item in docker.containers
        if item.labels.get(f"{NAMESPACE}.build-id") == "build-new"
    )


def test_rollout_waits_for_exact_registry_readiness_before_stopping_old_slot():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent()])
        docker = _Docker(clock)
        old = _container("old-0")
        docker.containers.append(old)
        store.registry[_ready(old).worker_id] = _ready(old)
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True
        replacement = _new_container(docker)
        assert old in docker.containers
        assert not any(event[0] == "stop" for event in docker.events)

        assert await reconciler.run_cycle() is False
        assert old in docker.containers

        store.registry[_ready(replacement).worker_id] = _ready(replacement)
        assert await reconciler.run_cycle() is True
        assert old in docker.containers
        assert ("blocked", _ready(old).worker_id) in store.events

        assert await reconciler.run_cycle() is True
        assert old not in docker.containers
        assert docker.events.index(
            ("start", replacement.container_id, "endpoint-1", 0, "build-new")
        ) < docker.events.index(("stop", "old-0"))

    asyncio.run(scenario())


def test_active_drain_waits_then_records_force_stop_timeout():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent()])
        docker = _Docker(clock)
        old = _container("old-0")
        new = _container(
            "new-0",
            build="build-new",
            revision="revision-new",
            image="sha256:new",
            generation=2,
        )
        docker.containers.extend([old, new])
        store.registry[_ready(new).worker_id] = _ready(new)
        old_snapshot = replace(_ready(old), status="running", task_id="task-1")
        store.registry[old_snapshot.worker_id] = old_snapshot
        reconciler = _reconciler(store, docker, clock, drain=10)

        assert await reconciler.run_cycle() is True
        assert old in docker.containers
        clock.advance(9)
        assert await reconciler.run_cycle() is False
        assert old in docker.containers
        clock.advance(1)
        assert await reconciler.run_cycle() is True
        assert old not in docker.containers
        removed = next(event for event in store.events if event[0] == "removed")
        assert removed[2] is True
        assert "timed out" in removed[3]

    asyncio.run(scenario())


def test_partial_rollout_readiness_failure_restores_missing_old_slots():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent(count=2)])
        docker = _Docker(clock)
        old_slot_1 = _container("old-1", slot=1)
        failed_new = _container(
            "new-0",
            slot=0,
            build="build-new",
            revision="revision-new",
            image="sha256:new",
            generation=2,
            created_at=clock(),
        )
        docker.containers.extend([old_slot_1, failed_new])
        store.registry[_ready(old_slot_1).worker_id] = _ready(old_slot_1)
        reconciler = _reconciler(store, docker, clock, readiness=5)

        clock.advance(5)
        assert await reconciler.run_cycle() is True
        assert store.intents[0].phase == "rollback"
        assert any(event[0] == "rollback" for event in store.events)
        assert old_slot_1 in docker.containers

        assert await reconciler.run_cycle() is True
        restored_slot_0 = next(
            item
            for item in docker.containers
            if item.labels.get(f"{NAMESPACE}.build-id") == "build-old"
            and item.labels.get(f"{NAMESPACE}.slot") == "0"
        )
        store.registry[_ready(restored_slot_0).worker_id] = _ready(restored_slot_0)
        assert await reconciler.run_cycle() is True
        assert failed_new in docker.containers
        assert await reconciler.run_cycle() is True
        assert failed_new not in docker.containers
        assert await reconciler.run_cycle() is True
        assert store.intents[0].phase == "ready"
        assert store.intents[0].active_build_id == "build-old"
        assert any(
            event == ("completed", "deployment-1", True) for event in store.events
        )

    asyncio.run(scenario())


def test_restart_adopts_exact_owned_container_and_ignores_similarly_named_foreign_resource():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent(phase="ready")])
        docker = _Docker(clock)
        owned = _container("owned-0")
        foreign = _container("foreign-0", labels={"name": owned.name})
        docker.containers.extend([owned, foreign])
        store.registry[_ready(owned).worker_id] = _ready(owned)
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is False
        assert await reconciler.run_cycle() is False
        assert [event for event in docker.events if event[0] == "start"] == []
        assert [event for event in docker.events if event[0] == "stop"] == []
        assert foreign in docker.containers
        assert "owned-0" in store.records
        assert "foreign-0" not in store.records

    asyncio.run(scenario())


def test_scaling_and_deletion_are_slot_deterministic_and_strictly_owned():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent(count=3, phase="ready")])
        docker = _Docker(clock)
        slot_0 = _container("owned-0")
        foreign = _container("foreign", labels={"lookalike": "dspy-ep-endpoint-1"})
        docker.containers.extend([slot_0, foreign])
        store.registry[_ready(slot_0).worker_id] = _ready(slot_0)
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True
        slot_1 = next(
            item
            for item in docker.containers
            if item.labels.get(f"{NAMESPACE}.slot") == "1"
        )
        store.registry[_ready(slot_1).worker_id] = _ready(slot_1)
        assert await reconciler.run_cycle() is True
        slot_2 = next(
            item
            for item in docker.containers
            if item.labels.get(f"{NAMESPACE}.slot") == "2"
        )
        store.registry[_ready(slot_2).worker_id] = _ready(slot_2)
        assert await reconciler.run_cycle() is False

        store.intents = [replace(store.intents[0], desired_replica_count=2)]
        assert await reconciler.run_cycle() is True
        assert await reconciler.run_cycle() is True
        assert slot_2 not in docker.containers
        assert slot_1 in docker.containers
        assert slot_0 in docker.containers

        store.intents = [replace(store.intents[0], desired_replica_count=1)]
        assert await reconciler.run_cycle() is True
        assert await reconciler.run_cycle() is True
        assert slot_1 not in docker.containers

        store.intents = []
        assert await reconciler.run_cycle() is True
        assert await reconciler.run_cycle() is True
        assert slot_0 not in docker.containers
        assert foreign in docker.containers

    asyncio.run(scenario())


def test_failed_start_keeps_old_revision_live_and_records_rollback():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent()])
        docker = _Docker(clock)
        old = _container("old-0")
        docker.containers.append(old)
        store.registry[_ready(old).worker_id] = _ready(old)
        docker.fail_start = True
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True
        assert old in docker.containers
        assert store.intents[0].phase == "rollback"
        assert any(
            "injected start failure" in event[2]
            for event in store.events
            if event[0] == "rollback"
        )

    asyncio.run(scenario())


def test_image_pruning_uses_only_store_candidates_after_convergence():
    async def scenario():
        clock = _Clock()
        store = _Store([])
        store.prune_candidates = [
            ImagePruneCandidate("sha256:old-1", "module-1", "build-1", "revision-1"),
            ImagePruneCandidate("sha256:old-2", "module-2", "build-2", "revision-2"),
        ]
        docker = _Docker(clock)
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True
        assert await reconciler.run_cycle() is True
        assert docker.pruned == ["sha256:old-1", "sha256:old-2"]

    asyncio.run(scenario())


def test_container_names_are_deterministic_and_collision_resistant():
    first = managed_container_name(
        deployment_owner=OWNER,
        endpoint_id="endpoint/with unsafe characters",
        slot=3,
        build_id="build-a",
        rollout_generation=4,
    )
    assert first == managed_container_name(
        deployment_owner=OWNER,
        endpoint_id="endpoint/with unsafe characters",
        slot=3,
        build_id="build-a",
        rollout_generation=4,
    )
    assert first != managed_container_name(
        deployment_owner=OWNER,
        endpoint_id="endpoint/with unsafe characters",
        slot=3,
        build_id="build-b",
        rollout_generation=4,
    )
    assert len(first) <= 63
    assert "/" not in first


class _NetworkCollection:
    def __init__(self, networks):
        self.networks = networks
        self.requested_names = []

    def list(self, *, names):
        self.requested_names.append(names)
        return self.networks


class _NetworkClient:
    def __init__(self, networks):
        self.networks = _NetworkCollection(networks)


def _network(network_id, name, project):
    return type(
        "Network",
        (),
        {
            "id": network_id,
            "name": name,
            "attrs": {
                "Name": name,
                "Labels": {"com.docker.compose.project": project},
            },
        },
    )()


def test_docker_adapter_requires_one_exact_compose_network():
    async def scenario():
        adapter = DockerSdkEndpointAdapter(
            deployment_id=OWNER,
            compose_project=PROJECT,
            label_namespace=NAMESPACE,
        )
        client = _NetworkClient(
            [
                _network("wrong-project", "runtime", "other-project"),
                _network("exact", "runtime", PROJECT),
                _network("wrong-name", "runtime-extra", PROJECT),
            ]
        )
        adapter._client = client

        assert await adapter.resolve_network("runtime", PROJECT) == "exact"
        assert client.networks.requested_names == [["runtime"]]

        adapter._client = _NetworkClient(
            [
                _network("first", "runtime", PROJECT),
                _network("second", "runtime", PROJECT),
            ]
        )
        with pytest.raises(RuntimeError, match="expected exactly one Compose network"):
            await adapter.resolve_network("runtime", PROJECT)

    asyncio.run(scenario())


class _QueueRedis:
    def __init__(self):
        self.commands = []

    async def execute_command(self, *args):
        self.commands.append(args)


class _ClaimServices:
    def __init__(self, claimed):
        self.claimed = claimed
        self.redis = _QueueRedis()
        self.claims = []

    async def claim_endpoint_worker_task(self, **kwargs):
        self.claims.append(kwargs)
        return self.claimed


def _raw_job(**changes):
    payload = {
        "type": "endpoint_invocation",
        "invocation_id": "invocation-1",
        "endpoint_id": "endpoint-1",
        "execution_mode": "managed_image",
        "build_id": "build-1",
        "revision_id": "revision-1",
        "bundle_path": "/opt/dspy-bundle",
    }
    payload.update(changes)
    return json.dumps(payload)


def _ready_target():
    return {
        "endpoint_id": "endpoint-1",
        "execution_mode": "managed_image",
        "build_id": "build-1",
        "revision_id": "revision-1",
        "bundle_path": "/opt/dspy-bundle",
    }


def test_wrong_identity_job_never_claims_or_requeues_for_execution():
    async def scenario():
        services = _ClaimServices(claimed=True)
        accepted = await claim_or_requeue_endpoint_job(
            services,
            queue_name="endpoint-queue",
            raw_payload=_raw_job(build_id="wrong-build"),
            worker_id="worker-1",
            ready_target=_ready_target(),
        )
        assert accepted is True
        assert services.claims == []
        assert services.redis.commands == []

    asyncio.run(scenario())


def test_drain_winning_before_atomic_claim_requeues_popped_job_exactly_once():
    async def scenario():
        services = _ClaimServices(claimed=False)
        raw = _raw_job()
        claimed = await claim_or_requeue_endpoint_job(
            services,
            queue_name="endpoint-queue",
            raw_payload=raw,
            worker_id="worker-1",
            ready_target=_ready_target(),
        )
        assert claimed is False
        assert services.redis.commands == [("RPUSH", "endpoint-queue", raw)]
        assert len(services.claims) == 1

    asyncio.run(scenario())


def test_atomic_claim_winning_before_drain_executes_without_requeue():
    async def scenario():
        services = _ClaimServices(claimed=True)
        raw = _raw_job()
        claimed = await claim_or_requeue_endpoint_job(
            services,
            queue_name="endpoint-queue",
            raw_payload=raw,
            worker_id="worker-1",
            ready_target=_ready_target(),
        )
        assert claimed is True
        assert services.redis.commands == []
        assert services.claims[0]["task_id"] == "invocation-1"

    asyncio.run(scenario())
