import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod
from app import revision_image_builder as image_contract
from app.endpoint_container_reconciler import (
    ContainerLaunchSpec,
    ContainerObservation,
    ContainerRecord,
    DeploymentIntent,
    DockerSdkEndpointAdapter,
    EndpointContainerReconciler,
    ImagePruneCandidate,
    ObservedContainer,
    PostgresEndpointContainerStore,
    RegistrySnapshot,
    managed_container_name,
)
from app.services import AppServices
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

    async def reconcile_deployment_targets(self):
        return 0

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

    async def finalize_endpoint_deletions(self):
        return False

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
        self.invalid_start_labels = False
        self.observation_complete = True
        self.fail_observation = False
        self._next_id = 1

    async def resolve_network(self, selector, compose_project):
        assert selector == "runtime-network"
        assert compose_project == PROJECT
        return "network-id"

    async def list_containers(self):
        if self.fail_observation:
            raise RuntimeError("injected Docker observation failure")
        return ContainerObservation(
            containers=tuple(self.containers),
            complete=self.observation_complete,
        )

    async def start_container(self, spec):
        if self.fail_start:
            raise RuntimeError("injected start failure")
        labels = dict(spec.labels)
        if self.invalid_start_labels:
            labels.pop(f"{NAMESPACE}.deployment-id")
        container = ObservedContainer(
            container_id=f"container-{self._next_id}",
            name=spec.name,
            image_id=spec.image_id,
            labels=labels,
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
    generation=2,
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
    generation=2,
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
        deployment_id=labels[f"{prefix}.deployment-id"],
        slot=int(labels[f"{prefix}.slot"]),
        rollout_generation=int(labels[f"{prefix}.rollout-generation"]),
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
        assert ("completed", "deployment-1", False) in store.events
        assert ("blocked", _ready(old).worker_id) not in store.events

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
        assert ("completed", "deployment-1", False) in store.events
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


def test_claimed_drain_stops_normally_after_worker_completion():
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
        assert ("completed", "deployment-1", False) in store.events
        assert await reconciler.run_cycle() is True
        assert old in docker.containers
        assert ("blocked", old_snapshot.worker_id) in store.events

        store.registry[old_snapshot.worker_id] = replace(
            store.registry[old_snapshot.worker_id], status="listening", task_id=None
        )
        assert await reconciler.run_cycle() is True
        assert old not in docker.containers
        removed = next(event for event in store.events if event[0] == "removed")
        assert removed[2] is False
        assert docker.events[-1] == ("stop", "old-0")

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
        assert store.intents[0].phase == "ready"
        assert await reconciler.run_cycle() is True
        assert failed_new in docker.containers
        assert await reconciler.run_cycle() is True
        assert failed_new not in docker.containers
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


def test_wrong_deployment_or_generation_is_never_adopted():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent(phase="ready")])
        docker = _Docker(clock)
        wrong_deployment = _container(
            "wrong-deployment",
            labels=_labels(deployment="old-deployment", generation=2),
        )
        wrong_generation = _container(
            "wrong-generation",
            labels=_labels(deployment="deployment-1", generation=1),
        )
        docker.containers.extend([wrong_deployment, wrong_generation])
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True
        replacement = next(
            item
            for item in docker.containers
            if item.container_id not in {"wrong-deployment", "wrong-generation"}
        )
        assert "wrong-deployment" not in store.records
        assert "wrong-generation" not in store.records
        assert replacement.labels[f"{NAMESPACE}.deployment-id"] == "deployment-1"
        assert replacement.labels[f"{NAMESPACE}.rollout-generation"] == "2"

    asyncio.run(scenario())


def test_repeated_non_running_replacements_hit_original_rollout_deadline():
    async def scenario():
        clock = _Clock()
        intent = replace(_intent(phase="rolling"), rollout_started_at=clock())
        store = _Store([intent])
        docker = _Docker(clock)
        old = _container("old-0")
        docker.containers.append(old)
        reconciler = _reconciler(store, docker, clock, readiness=5)

        assert await reconciler.run_cycle() is True
        first = _new_container(docker)
        docker.containers[docker.containers.index(first)] = replace(
            first, state="exited"
        )
        assert await reconciler.run_cycle() is True
        assert store.intents[0].phase == "rolling"

        clock.advance(4)
        assert await reconciler.run_cycle() is True
        second = _new_container(docker)
        docker.containers[docker.containers.index(second)] = replace(
            second, state="exited"
        )
        clock.advance(1)
        assert await reconciler.run_cycle() is True

        assert store.intents[0].phase == "rollback"
        assert old in docker.containers
        assert sum(event[0] == "start" for event in docker.events) == 2
        assert any(
            event[0] == "rollback" and "repeatedly stopped" in event[2]
            for event in store.events
        )

    asyncio.run(scenario())


def test_invalid_launch_identity_is_removed_and_rolls_back():
    async def scenario():
        clock = _Clock()
        store = _Store([_intent()])
        docker = _Docker(clock)
        old = _container("old-0")
        docker.containers.append(old)
        docker.invalid_start_labels = True
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True

        assert docker.containers == [old]
        assert ("stop", "container-1") in docker.events
        assert store.intents[0].phase == "rollback"
        assert any(
            event[0] == "rollback" and "exact required identity" in event[2]
            for event in store.events
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


class _AcquireConnection:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _ConnectionPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AcquireConnection(self.connection)


class _EndpointApiConnection:
    def __init__(self):
        self.endpoint = {
            "id": "endpoint-1",
            "module_import_id": "module-1",
            "lm_profile_id": None,
            "pinned_worker_count": 1,
            "name": "Managed endpoint",
            "key_preview": "abc123",
            "created_at": NOW,
            "updated_at": NOW,
            "module_bundle_name": "bundle",
            "lm_profile_name": None,
        }
        self.deployment = _intent(count=1, phase="ready")
        self.registry = {}
        self.container_records = {}
        self.tombstoned = False
        self.deleted = False
        self.worker_claims_blocked = False
        self.finalized = False
        self.prune_candidate = None
        self.pruned = False

    def is_closed(self):
        return False

    def transaction(self):
        return _AcquireConnection(self)

    async def fetchval(self, query, *params):
        return True

    async def fetch(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "with latest as" in normalized:
            if self.tombstoned or self.deleted:
                return []
            intent = self.deployment
            return [
                {
                    "id": intent.deployment_id,
                    "endpoint_id": intent.endpoint_id,
                    "endpoint_module_import_id": intent.module_id,
                    "phase": intent.phase,
                    "desired_replica_count": intent.desired_replica_count,
                    "rollout_generation": intent.rollout_generation,
                    "active_build_id": intent.active_build_id,
                    "active_revision_id": intent.active_revision_id,
                    "active_image_id": intent.active_image_id,
                    "active_module_import_id": intent.module_id,
                    "target_build_id": intent.target_build_id,
                    "target_revision_id": intent.target_revision_id,
                    "target_image_id": intent.target_image_id,
                    "target_module_import_id": (
                        intent.module_id if intent.target_build_id else None
                    ),
                    "previous_build_id": intent.previous_build_id,
                    "previous_revision_id": intent.previous_revision_id,
                    "previous_image_id": intent.previous_image_id,
                    "previous_module_import_id": (
                        intent.module_id if intent.previous_build_id else None
                    ),
                    "rollout_started_at": intent.rollout_started_at,
                }
            ]
        if "from endpoint_worker_registrations" in normalized:
            return [
                {
                    "worker_id": snapshot.worker_id,
                    "status": snapshot.status,
                    "assigned_endpoint_id": snapshot.assigned_endpoint_id,
                    "task_id": snapshot.task_id,
                    "last_seen_at": snapshot.last_seen_at or NOW,
                    "heartbeat_expires_at": NOW + timedelta(minutes=1),
                    "runtime_metadata": {
                        "endpoint_id": snapshot.reported_endpoint_id,
                        "execution_mode": snapshot.execution_mode,
                        "desired_build_id": snapshot.build_id,
                        "warmed_build_id": snapshot.build_id,
                        "desired_revision_id": snapshot.revision_id,
                        "warmed_revision_id": snapshot.revision_id,
                        "endpoint_deployment_id": snapshot.deployment_id,
                        "endpoint_slot": snapshot.slot,
                        "endpoint_rollout_generation": snapshot.rollout_generation,
                    },
                }
                for snapshot in self.registry.values()
            ]
        if (
            "from managed_endpoint_containers" in normalized
            and not normalized.startswith("with ranked as")
        ):
            return [
                record
                for record in self.container_records.values()
                if record["lifecycle"] != "removed"
            ]
        if "with ranked as" in normalized:
            has_reference = any(
                record["lifecycle"] != "removed"
                for record in self.container_records.values()
            )
            if self.prune_candidate is not None and not has_reference:
                return [dict(self.prune_candidate)]
            return []
        raise AssertionError(f"unexpected fetch SQL: {normalized}")

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "ready_build_id" in normalized and "from bundle_endpoints e" in normalized:
            return None
        if normalized.startswith("update bundle_endpoints set delete_requested_at"):
            if self.tombstoned or self.deleted:
                return None
            self.tombstoned = True
            self.endpoint["updated_at"] = params[2]
            return {"id": self.endpoint["id"]}
        if normalized.startswith("update bundle_endpoints set name"):
            if self.tombstoned or self.deleted:
                return None
            self.endpoint.update(
                name=params[1],
                lm_profile_id=params[2],
                pinned_worker_count=params[3],
                updated_at=params[4],
            )
            return dict(self.endpoint)
        if normalized.startswith("select e.id from bundle_endpoints e"):
            if self.deleted:
                return None
            active = any(
                record["lifecycle"] != "removed"
                for record in self.container_records.values()
            )
            if self.tombstoned and not active:
                return {"id": self.endpoint["id"]}
            return None
        if "from bundle_endpoints e" in normalized:
            if self.tombstoned or self.deleted:
                return None
            return dict(self.endpoint)
        raise AssertionError(f"unexpected fetchrow SQL: {normalized}")

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith(
            "update endpoint_deployments set desired_replica_count"
        ):
            self.deployment = replace(self.deployment, desired_replica_count=params[1])
            return "UPDATE 1"
        if normalized.startswith("update endpoint_deployments set phase = 'draining'"):
            self.deployment = replace(self.deployment, phase="draining")
            return "UPDATE 1"
        if normalized.startswith("insert into managed_endpoint_containers"):
            self.container_records[params[0]] = {
                "container_id": params[0],
                "worker_id": params[7],
                "lifecycle": params[8],
                "started_at": params[10],
                "drain_started_at": None,
            }
            return "INSERT 1"
        if normalized.startswith(
            "update managed_endpoint_containers set lifecycle = 'draining'"
        ):
            record = self.container_records[params[0]]
            record["lifecycle"] = "draining"
            record["drain_started_at"] = record["drain_started_at"] or params[1]
            return "UPDATE 1"
        if normalized.startswith(
            "update managed_endpoint_containers set lifecycle = 'removed'"
        ):
            self.container_records[params[0]]["lifecycle"] = "removed"
            return "UPDATE 1"
        if normalized.startswith("with blocked as"):
            worker_id = params[0]
            for record in self.container_records.values():
                if record["worker_id"] == worker_id and record["lifecycle"] in {
                    "created",
                    "starting",
                    "ready",
                    "busy",
                }:
                    record["lifecycle"] = "draining"
                    record["drain_started_at"] = record["drain_started_at"] or NOW
            self.worker_claims_blocked = True
            snapshot = self.registry.get(worker_id)
            if snapshot is not None:
                self.registry[worker_id] = replace(snapshot, assigned_endpoint_id=None)
            return "UPDATE 1"
        if normalized.startswith("update endpoint_worker_registrations"):
            self.worker_claims_blocked = True
            return "UPDATE 1"
        if normalized.startswith("delete from managed_endpoint_containers"):
            self.container_records.clear()
            return "DELETE 2"
        if normalized.startswith("delete from endpoint_deployments"):
            return "DELETE 1"
        if normalized.startswith("update revision_image_builds"):
            self.pruned = True
            return "UPDATE 1"
        if normalized.startswith("delete from bundle_endpoints"):
            self.deleted = True
            self.finalized = True
            return "DELETE 1"
        raise AssertionError(f"unexpected execute SQL: {normalized}")


def _endpoint_store(connection):
    store = PostgresEndpointContainerStore(
        postgres_dsn="postgresql://unused",
        instance_id="test",
        leader_timeout_seconds=15,
    )
    store._connection = connection
    return store


def test_endpoint_api_updates_managed_scale_and_tombstones_before_reconcile_delete(
    monkeypatch,
):
    connection = _EndpointApiConnection()
    services = AppServices(SimpleNamespace())
    services.postgres_pool = _ConnectionPool(connection)

    async def no_op(*args, **kwargs):
        return None

    async def get_module(module_id):
        return {"id": module_id}

    async def get_deployment_status(endpoint_id):
        return {"endpoint_id": endpoint_id, "phase": "ready"}

    services.connect_backend = no_op
    services.disconnect = no_op
    services.reconcile_endpoint_worker_assignments = no_op
    services.get_module = get_module
    services.get_endpoint_deployment_status = get_deployment_status
    monkeypatch.setattr(main_mod, "AppServices", lambda settings: services)
    monkeypatch.setattr(main_mod, "get_settings", lambda: SimpleNamespace())

    with TestClient(main_mod.app) as client:
        scaled = client.patch(
            "/bundle-endpoints/endpoint-1", json={"pinned_worker_count": 2}
        )
        assert scaled.status_code == 200
        assert scaled.json()["pinned_worker_count"] == 2

    clock = _Clock()
    store = _endpoint_store(connection)
    docker = _Docker(clock)
    slot_0 = _container("owned-0")
    docker.containers.append(slot_0)
    connection.registry[_ready(slot_0).worker_id] = _ready(slot_0)
    reconciler = _reconciler(store, docker, clock)

    async def reconcile_scaled_endpoint():
        assert await reconciler.run_cycle() is True
        slot_1 = next(
            item
            for item in docker.containers
            if item.labels.get(f"{NAMESPACE}.slot") == "1"
        )
        connection.registry[_ready(slot_1).worker_id] = _ready(slot_1)
        assert await reconciler.run_cycle() is False
        return slot_1

    slot_1 = asyncio.run(reconcile_scaled_endpoint())
    assert connection.deployment.desired_replica_count == 2
    assert slot_1 in docker.containers

    with TestClient(main_mod.app) as client:
        deleted = client.delete("/bundle-endpoints/endpoint-1")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

    assert connection.tombstoned is True
    assert connection.deleted is False
    assert connection.deployment.phase == "draining"
    assert connection.worker_claims_blocked is True

    async def reconcile_deleted_endpoint():
        for _ in range(8):
            await reconciler.run_cycle()
            if connection.deleted:
                return
        raise AssertionError("tombstoned endpoint did not finalize")

    asyncio.run(reconcile_deleted_endpoint())
    assert docker.containers == []
    assert connection.deleted is True
    assert connection.finalized is True


def _missing_container_restart_state():
    connection = _EndpointApiConnection()
    connection.tombstoned = True
    connection.deployment = replace(connection.deployment, phase="draining")
    connection.container_records["missing-container"] = {
        "container_id": "missing-container",
        "lifecycle": "ready",
        "started_at": NOW,
        "drain_started_at": None,
    }
    connection.prune_candidate = {
        "id": "released-build",
        "revision_id": "released-revision",
        "module_import_id": "module-1",
        "image_id": "sha256:released",
    }
    return connection


def test_complete_restart_observation_finalizes_missing_row_endpoint_and_image():
    async def scenario():
        connection = _missing_container_restart_state()
        store = _endpoint_store(connection)
        clock = _Clock()
        docker = _Docker(clock)
        foreign = replace(
            _container(
                "foreign",
                labels={
                    f"{NAMESPACE}.owner": OWNER,
                    f"{NAMESPACE}.managed-kind": "endpoint-worker",
                    f"{NAMESPACE}.endpoint-id": "endpoint-1",
                },
            ),
            name="container-name-missing-container",
        )
        docker.containers.append(foreign)
        reconciler = _reconciler(store, docker, clock)

        assert await reconciler.run_cycle() is True
        assert (
            connection.container_records["missing-container"]["lifecycle"] == "removed"
        )
        assert foreign in docker.containers
        assert ("stop", "foreign") not in docker.events

        assert await reconciler.run_cycle() is True
        assert connection.deleted is True
        assert connection.container_records == {}

        assert await reconciler.run_cycle() is True
        assert docker.pruned == ["sha256:released"]
        assert connection.pruned is True
        assert foreign in docker.containers

    asyncio.run(scenario())


def test_incomplete_restart_observation_preserves_row_endpoint_and_image():
    async def scenario():
        connection = _missing_container_restart_state()
        store = _endpoint_store(connection)
        docker = _Docker(_Clock())
        docker.observation_complete = False
        reconciler = _reconciler(store, docker, _Clock())

        assert await reconciler.run_cycle() is False
        assert connection.container_records["missing-container"]["lifecycle"] == "ready"
        assert connection.deleted is False
        assert connection.pruned is False
        assert docker.pruned == []

    asyncio.run(scenario())


def test_failed_restart_observation_preserves_row_endpoint_and_image():
    async def scenario():
        connection = _missing_container_restart_state()
        store = _endpoint_store(connection)
        docker = _Docker(_Clock())
        docker.fail_observation = True
        reconciler = _reconciler(store, docker, _Clock())

        with pytest.raises(RuntimeError, match="observation failure"):
            await reconciler.run_cycle()
        assert connection.container_records["missing-container"]["lifecycle"] == "ready"
        assert connection.deleted is False
        assert connection.pruned is False

    asyncio.run(scenario())


class _PruneSqlConnection:
    def __init__(self):
        self.query = ""
        self.params = ()

    def is_closed(self):
        return False

    async def fetch(self, query, *params):
        self.query = " ".join(query.split())
        self.params = params
        if "b.module_import_id" in self.query:
            raise RuntimeError("column b.module_import_id does not exist")
        if (
            "join bundle_revisions r on r.id = b.revision_id" not in self.query
            or "partition by r.module_import_id" not in self.query
        ):
            raise RuntimeError(
                "module ownership was not resolved through bundle_revisions"
            )
        return [
            {
                "id": "build-old",
                "revision_id": "revision-old",
                "module_import_id": "module-1",
                "image_id": "sha256:old",
            }
        ]


def test_postgres_prune_candidates_join_revision_for_module_ownership():
    async def scenario():
        connection = _PruneSqlConnection()
        store = PostgresEndpointContainerStore(
            postgres_dsn="postgresql://unused",
            instance_id="test",
            leader_timeout_seconds=15,
        )
        store._connection = connection

        candidates = await store.list_image_prune_candidates(2)

        assert candidates == [
            ImagePruneCandidate("sha256:old", "module-1", "build-old", "revision-old")
        ]
        assert connection.params == (2,)

    asyncio.run(scenario())


class _QueueRedis:
    def __init__(self):
        self.commands = []

    async def execute_command(self, *args):
        self.commands.append(args)


class _AtomicRegistrationConnection:
    def __init__(self, winner):
        self.winner = winner
        self.lock = asyncio.Lock()
        self.first_entered = asyncio.Event()
        self.release_first = asyncio.Event()
        self.claim_calls = 0
        self.active_seen_by_drain = False
        self.registration = {
            "worker_id": "worker-1",
            "assigned_endpoint_id": "endpoint-1",
            "status": "listening",
            "task_id": None,
        }

    def is_closed(self):
        return False

    async def fetchrow(self, query, *params):
        assert "update endpoint_worker_registrations" in query
        async with self.lock:
            self.claim_calls += 1
            if self.winner == "claim":
                self.first_entered.set()
                await self.release_first.wait()
            registration = self.registration
            if not (
                registration["worker_id"] == params[0]
                and registration["assigned_endpoint_id"] == params[1]
                and registration["status"] == "listening"
                and registration["task_id"] is None
            ):
                return None
            registration["status"] = "running"
            registration["task_id"] = params[2]
            return {"worker_id": params[0]}

    async def execute(self, query, *params):
        assert "set assigned_endpoint_id = null" in query
        async with self.lock:
            if self.winner == "drain":
                self.first_entered.set()
                await self.release_first.wait()
            self.active_seen_by_drain = bool(
                self.registration["status"] == "running"
                and self.registration["task_id"]
            )
            if self.registration["worker_id"] == params[0]:
                self.registration["assigned_endpoint_id"] = None
        return "UPDATE 1"


def _claim_services(connection):
    services = object.__new__(AppServices)
    services.settings = SimpleNamespace(endpoint_worker_heartbeat_ttl_seconds=30)
    services.postgres_pool = _ConnectionPool(connection)
    services.redis = _QueueRedis()
    return services


def _claim_store(connection):
    store = PostgresEndpointContainerStore(
        postgres_dsn="postgresql://unused",
        instance_id="test",
        leader_timeout_seconds=15,
    )
    store._connection = connection
    return store


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
        connection = _AtomicRegistrationConnection("claim")
        services = _claim_services(connection)
        accepted = await claim_or_requeue_endpoint_job(
            services,
            queue_name="endpoint-queue",
            raw_payload=_raw_job(build_id="wrong-build"),
            worker_id="worker-1",
            ready_target=_ready_target(),
        )
        assert accepted is True
        assert connection.claim_calls == 0
        assert services.redis.commands == []

    asyncio.run(scenario())


def test_drain_winning_atomic_registry_update_requeues_exact_job_once():
    async def scenario():
        connection = _AtomicRegistrationConnection("drain")
        services = _claim_services(connection)
        store = _claim_store(connection)
        raw = _raw_job()

        drain_task = asyncio.create_task(store.block_worker_claims("worker-1"))
        await connection.first_entered.wait()
        claim_task = asyncio.create_task(
            claim_or_requeue_endpoint_job(
                services,
                queue_name="endpoint-queue",
                raw_payload=raw,
                worker_id="worker-1",
                ready_target=_ready_target(),
            )
        )
        await asyncio.sleep(0)
        connection.release_first.set()
        await drain_task
        claimed = await claim_task

        executed = claimed
        assert executed is False
        assert connection.registration["task_id"] is None
        assert services.redis.commands == [("RPUSH", "endpoint-queue", raw)]
        assert connection.claim_calls == 1

    asyncio.run(scenario())


def test_atomic_registry_claim_is_visible_before_drain_and_never_requeues():
    async def scenario():
        connection = _AtomicRegistrationConnection("claim")
        services = _claim_services(connection)
        store = _claim_store(connection)
        raw = _raw_job()

        claim_task = asyncio.create_task(
            claim_or_requeue_endpoint_job(
                services,
                queue_name="endpoint-queue",
                raw_payload=raw,
                worker_id="worker-1",
                ready_target=_ready_target(),
            )
        )
        await connection.first_entered.wait()
        drain_task = asyncio.create_task(store.block_worker_claims("worker-1"))
        await asyncio.sleep(0)
        connection.release_first.set()
        claimed = await claim_task
        await drain_task

        assert claimed is True
        assert connection.active_seen_by_drain is True
        assert connection.registration["task_id"] == "invocation-1"
        assert services.redis.commands == []

    asyncio.run(scenario())


def test_managed_container_launch_uses_only_immutable_image_and_compose_network():
    image_id = f"sha256:{'d' * 64}"
    image_labels = {key: "value" for key in image_contract.REQUIRED_IMAGE_LABELS}
    image_labels.update(
        {
            image_contract.LABEL_PLATFORM_OWNER: image_contract.PLATFORM_OWNER,
            image_contract.LABEL_OWNER: OWNER,
            image_contract.LABEL_MANAGED_KIND: image_contract.MANAGED_IMAGE_KIND,
            image_contract.LABEL_MODULE_ID: "module-1",
            image_contract.LABEL_BUILD_ID: "build-1",
            image_contract.LABEL_REVISION_ID: "revision-1",
        }
    )
    launch_labels = _labels(
        build="build-1",
        revision="revision-1",
        image=image_id,
    )

    class Container:
        id = "container-1"
        name = "managed-1"
        status = "running"
        attrs = {
            "Id": id,
            "Name": f"/{name}",
            "Image": image_id,
            "Config": {"Labels": launch_labels},
            "State": {"Status": "running"},
            "Created": NOW.isoformat(),
        }

        def reload(self):
            return None

    class Containers:
        def __init__(self):
            self.kwargs = None

        def run(self, requested_image_id, **kwargs):
            assert requested_image_id == image_id
            self.kwargs = kwargs
            return Container()

    containers = Containers()
    client = SimpleNamespace(
        images=SimpleNamespace(
            get=lambda requested: SimpleNamespace(
                id=requested,
                attrs={"Config": {"Labels": image_labels}},
            )
        ),
        containers=containers,
    )
    adapter = DockerSdkEndpointAdapter(
        deployment_id=OWNER,
        compose_project=PROJECT,
        label_namespace=NAMESPACE,
    )
    adapter._client = client
    spec = ContainerLaunchSpec(
        name="managed-1",
        worker_id="worker-1",
        image_id=image_id,
        module_id="module-1",
        endpoint_id="endpoint-1",
        deployment_id="deployment-1",
        build_id="build-1",
        revision_id="revision-1",
        slot=0,
        rollout_generation=2,
        network_id="network-id",
        labels=launch_labels,
        environment={"DSPY_TRAINER_REDIS_URL": "redis://redis:6379/0"},
    )

    observed = asyncio.run(adapter.start_container(spec))

    assert observed.image_id == image_id
    assert containers.kwargs["network"] == "network-id"
    assert "volumes" not in containers.kwargs
    assert "mounts" not in containers.kwargs
    assert all("CHECKOUT" not in key for key in containers.kwargs["environment"])
