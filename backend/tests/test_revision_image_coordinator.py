from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.revision_image_builder import (
    BuildCancellation,
    RevisionImageBuildCancelled,
    RevisionImageBuildResult,
)
from app.revision_image_coordinator import (
    PostgresRevisionImageBuildStore,
    RevisionImageBuildClaim,
    RevisionImageBuildCoordinator,
)

BASE_IMAGE_ID = f"sha256:{'a' * 64}"
IMAGE_ID = f"sha256:{'b' * 64}"


@dataclass
class _Record:
    build_id: str
    module_id: str
    revision_id: str
    generation: int = 1
    status: str = "queued"
    serving: bool = False
    available_at: float = 0
    queued_at: float = 0
    attempt: int = 0
    claim_owner: str | None = None
    claim_expires_at: float | None = None
    local_tag: str = "revision:test"
    build_log: str = ""
    failure_reason: str | None = None
    image_id: str | None = None
    superseded: bool = False


class _SharedDatabase:
    def __init__(self, records: list[_Record]) -> None:
        self.records = {record.build_id: record for record in records}
        self.leader_owner: str | None = None
        self.now = 100.0
        self.lock = asyncio.Lock()


class _FakeStore:
    def __init__(
        self, database: _SharedDatabase, instance_id: str, *, max_log_bytes: int = 256
    ) -> None:
        self.database = database
        self.instance_id = instance_id
        self.max_log_bytes = max_log_bytes
        self.connected = True

    async def try_acquire_leadership(self) -> bool:
        async with self.database.lock:
            if self.database.leader_owner not in {None, self.instance_id}:
                return False
            self.database.leader_owner = self.instance_id
            self.connected = True
            return True

    async def release_leadership(self) -> None:
        async with self.database.lock:
            if self.database.leader_owner == self.instance_id:
                self.database.leader_owner = None

    async def disconnect(self) -> None:
        self.connected = False
        await self.release_leadership()

    async def recover_expired_claims(self) -> int:
        recovered = 0
        async with self.database.lock:
            for record in self.database.records.values():
                if (
                    record.status == "building"
                    and record.claim_expires_at is not None
                    and record.claim_expires_at <= self.database.now
                ):
                    record.status = "queued"
                    record.claim_owner = None
                    record.claim_expires_at = None
                    record.build_log = self._bounded(
                        record.build_log + "claim expired; requeued for recovery\n"
                    )
                    recovered += 1
        return recovered

    async def reconcile_eligible_revisions(self) -> int:
        async with self.database.lock:
            for record in self.database.records.values():
                if record.status == "queued" and record.superseded:
                    record.status = "failed"
                    record.failure_reason = "superseded by a newer eligible revision"
        return 0

    async def claim_next(
        self, *, claim_timeout_seconds: float
    ) -> RevisionImageBuildClaim | None:
        async with self.database.lock:
            if any(
                record.status == "building" for record in self.database.records.values()
            ):
                return None
            queued = [
                record
                for record in self.database.records.values()
                if record.status == "queued"
                and record.available_at <= self.database.now
            ]
            if not queued:
                return None
            record = min(
                queued,
                key=lambda item: (
                    0 if item.serving else 1,
                    item.available_at,
                    item.queued_at,
                    item.build_id,
                ),
            )
            record.status = "building"
            record.attempt += 1
            record.claim_owner = self.instance_id
            record.claim_expires_at = self.database.now + claim_timeout_seconds
            return self._claim(record)

    async def renew_claim(
        self, claim: RevisionImageBuildClaim, *, claim_timeout_seconds: float
    ) -> bool:
        async with self.database.lock:
            record = self.database.records[claim.build_id]
            if not self._owns(record, claim):
                return False
            record.claim_expires_at = self.database.now + claim_timeout_seconds
            return True

    async def claim_cancellation_reason(
        self, claim: RevisionImageBuildClaim
    ) -> str | None:
        async with self.database.lock:
            record = self.database.records[claim.build_id]
            if not self._owns(record, claim):
                return "claim ownership lost"
            if record.superseded:
                return "build superseded because the revision is no longer eligible"
            return None

    async def append_claim_log(self, claim: RevisionImageBuildClaim, text: str) -> bool:
        async with self.database.lock:
            record = self.database.records[claim.build_id]
            if not self._owns(record, claim):
                return False
            record.build_log = self._bounded(record.build_log + text)
            return True

    async def complete_claim(
        self, claim: RevisionImageBuildClaim, result: RevisionImageBuildResult
    ) -> bool:
        async with self.database.lock:
            record = self.database.records[claim.build_id]
            if not self._owns(record, claim):
                return False
            record.status = result.status
            record.claim_owner = None
            record.claim_expires_at = None
            record.build_log = self._bounded(result.build_log)
            record.failure_reason = result.failure_reason
            record.image_id = result.image_id
            if result.status == "ready":
                for previous in self.database.records.values():
                    if (
                        previous.build_id != record.build_id
                        and previous.revision_id == record.revision_id
                        and previous.status == "ready"
                    ):
                        previous.status = "superseded"
            return True

    async def fail_claim(
        self, claim: RevisionImageBuildClaim, reason: str, build_log: str = ""
    ) -> bool:
        async with self.database.lock:
            record = self.database.records[claim.build_id]
            if not self._owns(record, claim):
                return False
            record.status = "failed"
            record.claim_owner = None
            record.claim_expires_at = None
            record.failure_reason = reason
            if build_log:
                record.build_log = self._bounded(build_log)
            return True

    async def release_claim(self, claim: RevisionImageBuildClaim) -> bool:
        async with self.database.lock:
            record = self.database.records[claim.build_id]
            if not self._owns(record, claim):
                return False
            record.status = "queued"
            record.claim_owner = None
            record.claim_expires_at = None
            record.build_log = self._bounded(
                record.build_log + "deployer stopped; requeued for recovery\n"
            )
            return True

    def _claim(self, record: _Record) -> RevisionImageBuildClaim:
        return RevisionImageBuildClaim(
            build_id=record.build_id,
            module_id=record.module_id,
            revision_id=record.revision_id,
            generation=record.generation,
            source_commit="commit",
            source_snapshot_path=Path("/snapshot") / record.revision_id,
            source_content_digest=f"sha256:{'c' * 64}",
            local_tag=record.local_tag,
            base_image_id=BASE_IMAGE_ID,
            attempt=record.attempt,
            claim_owner=str(record.claim_owner),
        )

    @staticmethod
    def _owns(record: _Record, claim: RevisionImageBuildClaim) -> bool:
        return (
            record.status == "building"
            and record.claim_owner == claim.claim_owner
            and record.attempt == claim.attempt
        )

    def _bounded(self, value: str) -> str:
        data = value.encode()
        return data[-self.max_log_bytes :].decode(errors="ignore")


class _BuildConcurrency:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class _FakeBuilder:
    def __init__(
        self,
        tags: dict[str, str],
        *,
        block: bool = False,
        ignore_cancellation: bool = False,
        fail: bool = False,
        concurrency: _BuildConcurrency | None = None,
    ) -> None:
        self.tags = tags
        self.block = block
        self.ignore_cancellation = ignore_cancellation
        self.fail = fail
        self.concurrency = concurrency or _BuildConcurrency()
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel_count = 0
        self.built: list[str] = []

    def build(self, spec, cancellation=None, on_log=None):
        cancellation = cancellation or BuildCancellation()
        self.concurrency.enter()
        self.started.set()
        self.built.append(spec.build_id)
        try:
            if on_log is not None:
                on_log(f"building {spec.build_id}\n")
            while self.block and not self.release.wait(0.005):
                if cancellation.cancelled and not self.ignore_cancellation:
                    raise RevisionImageBuildCancelled("cancelled")
            if cancellation.cancelled and not self.ignore_cancellation:
                raise RevisionImageBuildCancelled("cancelled")
            if self.fail:
                return _result(
                    self.tags[spec.build_id],
                    status="failed",
                    failure_reason="Docker Engine unavailable",
                )
            return _result(self.tags[spec.build_id])
        finally:
            self.concurrency.leave()

    def cancel(self, cancellation: BuildCancellation) -> None:
        self.cancel_count += 1
        cancellation.cancel()


def _result(
    local_tag: str, *, status: str = "ready", failure_reason: str | None = None
):
    return RevisionImageBuildResult(
        status=status,
        local_tag=local_tag,
        build_digest=f"sha256:{'d' * 64}",
        dependency_digest=f"sha256:{'e' * 64}",
        image_id=IMAGE_ID if status == "ready" else None,
        image_digest=None,
        labels={},
        build_log="docker output\n",
        failure_reason=failure_reason,
    )


def _coordinator(
    store: _FakeStore, builder: _FakeBuilder
) -> RevisionImageBuildCoordinator:
    return RevisionImageBuildCoordinator(
        store=store,
        builder=builder,
        deployment_id="deployment-a",
        image_repository="revision",
        platform_version="test",
        claim_timeout_seconds=0.3,
        poll_interval_seconds=0.01,
        build_log_max_bytes=store.max_log_bytes,
    )


async def _wait_started(builder: _FakeBuilder) -> None:
    started = await asyncio.wait_for(
        asyncio.to_thread(builder.started.wait, 1), timeout=2
    )
    assert started


def test_only_the_advisory_leader_builds_and_a_successor_recovers_the_released_claim():
    async def scenario():
        record = _Record("build-1", "module", "revision", local_tag="revision:one")
        database = _SharedDatabase([record])
        concurrency = _BuildConcurrency()
        leader_builder = _FakeBuilder(
            {record.build_id: record.local_tag}, block=True, concurrency=concurrency
        )
        standby_builder = _FakeBuilder(
            {record.build_id: record.local_tag}, concurrency=concurrency
        )
        leader_store = _FakeStore(database, "leader")
        standby_store = _FakeStore(database, "standby")
        leader = _coordinator(leader_store, leader_builder)
        standby = _coordinator(standby_store, standby_builder)

        leader_cycle = asyncio.create_task(leader.run_cycle())
        await _wait_started(leader_builder)
        assert await standby.run_cycle() is False
        assert standby_builder.built == []
        assert concurrency.maximum == 1

        leader.request_stop()
        await leader_cycle
        assert record.status == "queued"
        await leader_store.release_leadership()

        assert await standby.run_cycle() is True
        assert record.status == "ready"
        assert record.attempt == 2
        assert concurrency.maximum == 1

    asyncio.run(scenario())


def test_serving_revisions_are_prioritized_then_equal_priority_builds_are_fifo():
    async def scenario():
        later = _Record(
            "build-z", "plain", "revision-z", queued_at=2, local_tag="revision:z"
        )
        earlier = _Record(
            "build-a", "plain", "revision-a", queued_at=1, local_tag="revision:a"
        )
        serving = _Record(
            "build-s",
            "serving",
            "revision-s",
            serving=True,
            queued_at=3,
            local_tag="revision:s",
        )
        database = _SharedDatabase([later, earlier, serving])
        builder = _FakeBuilder(
            {record.build_id: record.local_tag for record in (later, earlier, serving)}
        )
        coordinator = _coordinator(_FakeStore(database, "leader"), builder)

        await coordinator.run_cycle()
        await coordinator.run_cycle()
        await coordinator.run_cycle()

        assert builder.built == ["build-s", "build-a", "build-z"]

    asyncio.run(scenario())


def test_expired_claim_is_requeued_and_reclaimed_with_a_new_fencing_attempt():
    async def scenario():
        record = _Record(
            "build-1",
            "module",
            "revision",
            status="building",
            attempt=1,
            claim_owner="dead-leader",
            claim_expires_at=99,
            local_tag="revision:one",
        )
        database = _SharedDatabase([record])
        builder = _FakeBuilder({record.build_id: record.local_tag})
        coordinator = _coordinator(_FakeStore(database, "successor"), builder)

        await coordinator.run_cycle()

        assert record.status == "ready"
        assert record.attempt == 2
        assert (
            "claim expired" in record.build_log or record.build_log == "docker output\n"
        )

    asyncio.run(scenario())


def test_queued_and_active_supersession_skip_old_work_and_cancel_the_active_adapter():
    async def scenario():
        queued_old = _Record(
            "old-queued",
            "module-q",
            "revision-q1",
            superseded=True,
            local_tag="revision:q1",
        )
        queued_new = _Record(
            "new-queued",
            "module-q",
            "revision-q2",
            queued_at=2,
            local_tag="revision:q2",
        )
        database = _SharedDatabase([queued_old, queued_new])
        first_builder = _FakeBuilder(
            {
                queued_old.build_id: queued_old.local_tag,
                queued_new.build_id: queued_new.local_tag,
            }
        )
        first = _coordinator(_FakeStore(database, "leader"), first_builder)

        await first.run_cycle()
        assert queued_old.status == "failed"
        assert first_builder.built == ["new-queued"]

        active_old = _Record(
            "old-active", "module-a", "revision-a1", local_tag="revision:a1"
        )
        database.records[active_old.build_id] = active_old
        blocking = _FakeBuilder({active_old.build_id: active_old.local_tag}, block=True)
        active = _coordinator(_FakeStore(database, "leader"), blocking)
        active._leader = True
        cycle = asyncio.create_task(active.run_cycle())
        await _wait_started(blocking)
        active_old.superseded = True
        await cycle

        assert blocking.cancel_count == 1
        assert active_old.status == "failed"
        assert "superseded" in (active_old.failure_reason or "")

    asyncio.run(scenario())


def test_late_completion_is_fenced_after_claim_ownership_changes():
    async def scenario():
        record = _Record("build-1", "module", "revision", local_tag="revision:one")
        database = _SharedDatabase([record])
        builder = _FakeBuilder(
            {record.build_id: record.local_tag},
            block=True,
            ignore_cancellation=True,
        )
        coordinator = _coordinator(_FakeStore(database, "leader"), builder)

        cycle = asyncio.create_task(coordinator.run_cycle())
        await _wait_started(builder)
        record.claim_owner = "successor"
        record.attempt += 1
        await asyncio.sleep(0.03)
        builder.release.set()
        await cycle

        assert builder.cancel_count == 1
        assert record.status == "building"
        assert record.claim_owner == "successor"
        assert record.image_id is None

    asyncio.run(scenario())


def test_clean_shutdown_cancels_the_builder_and_requeues_owned_work():
    async def scenario():
        record = _Record("build-1", "module", "revision", local_tag="revision:one")
        database = _SharedDatabase([record])
        builder = _FakeBuilder({record.build_id: record.local_tag}, block=True)
        coordinator = _coordinator(_FakeStore(database, "leader"), builder)

        cycle = asyncio.create_task(coordinator.run_cycle())
        await _wait_started(builder)
        coordinator.request_stop()
        await cycle

        assert builder.cancel_count >= 1
        assert record.status == "queued"
        assert record.claim_owner is None

    asyncio.run(scenario())


def test_docker_outage_fails_only_the_new_generation_and_preserves_prior_ready_image():
    async def scenario():
        previous = _Record(
            "build-1",
            "module",
            "revision",
            generation=1,
            status="ready",
            local_tag="revision:one",
            image_id=f"sha256:{'1' * 64}",
        )
        replacement = _Record(
            "build-2",
            "module",
            "revision",
            generation=2,
            local_tag="revision:two",
        )
        database = _SharedDatabase([previous, replacement])
        builder = _FakeBuilder({replacement.build_id: replacement.local_tag}, fail=True)
        coordinator = _coordinator(_FakeStore(database, "leader"), builder)

        await coordinator.run_cycle()

        assert replacement.status == "failed"
        assert replacement.failure_reason == "Docker Engine unavailable"
        assert previous.status == "ready"
        assert previous.image_id == f"sha256:{'1' * 64}"

    asyncio.run(scenario())
def test_closed_leader_session_must_reacquire_before_more_queue_work():
    class ClosedConnection:
        @staticmethod
        def is_closed():
            return True

    async def scenario():
        store = PostgresRevisionImageBuildStore(
            postgres_dsn="postgresql://unused",
            instance_id="instance-a",
            deployment_id="deployment-a",
            base_image_id=BASE_IMAGE_ID,
            image_repository="revision",
            platform_version="test",
            build_log_max_bytes=256,
            leader_timeout_seconds=1,
        )
        store._connection = ClosedConnection()
        store._leader = True

        try:
            await store._conn()
        except ConnectionError:
            pass
        else:
            raise AssertionError("closed advisory-lock session was silently replaced")

        assert store._leader is False
        assert store._connection is None

    asyncio.run(scenario())
