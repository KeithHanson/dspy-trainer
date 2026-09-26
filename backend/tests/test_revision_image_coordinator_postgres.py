from __future__ import annotations

import asyncio
import pytest
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.revision_image_builder import (
    RevisionImageBuildResult,
    calculate_source_content_digest,
)
from app.revision_image_coordinator import (
    PostgresRevisionImageBuildStore,
    RevisionImageBuildClaim,
    RevisionImageEnqueueError,
)

BASE_IMAGE_ID = f"sha256:{'a' * 64}"
IMAGE_ID = f"sha256:{'b' * 64}"


class _Transaction:
    def __init__(self, lock):
        self._lock = lock

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self._lock.release()
        return False


class _StatefulPostgresConnection:
    """Small relational model that applies the store's observable transitions."""

    def __init__(self) -> None:
        self.now = 100.0
        self._transaction_lock = asyncio.Lock()
        self.modules: dict[str, dict] = {}
        self.revisions: dict[str, dict] = {}
        self.runtime: dict[str, dict] = {}
        self.builds: dict[str, dict] = {}
        self.endpoint_modules: set[str] = set()
        self.deployment_revisions: set[str] = set()
        self.deployer_base_image_id = BASE_IMAGE_ID

    def is_closed(self):
        return False

    def transaction(self):
        return _Transaction(self._transaction_lock)

    async def execute(self, sql, *args):
        query = " ".join(sql.lower().split())

        if "select pg_advisory_xact_lock($1)" in query:
            return "SELECT 1"

        if "where status = 'building' and claim_expires_at <= now()" in query:
            count = 0
            recovery_log, max_bytes = args
            for build in self.builds.values():
                if (
                    build["status"] == "building"
                    and build["claim_expires_at"] is not None
                    and build["claim_expires_at"] <= self.now
                ):
                    build["status"] = "queued"
                    build["available_at"] = self.now
                    build["claim_owner"] = None
                    build["claim_expires_at"] = None
                    combined = f"{build['build_log']}{recovery_log}"
                    build["build_log"] = (
                        combined
                        if len(combined.encode()) <= max_bytes
                        else recovery_log
                    )
                    build["failure_reason"] = None
                    count += 1
            return f"UPDATE {count}"

        if "from bundle_revisions br, eligible e" in query:
            eligible_by_module = {
                revision["module_id"]: revision_id
                for revision_id, revision in self.revisions.items()
                if self._revision_eligible(revision_id)
            }
            count = 0
            for build in self.builds.values():
                revision = self.revisions[build["revision_id"]]
                eligible_revision = eligible_by_module.get(revision["module_id"])
                if (
                    build["status"] == "queued"
                    and eligible_revision is not None
                    and build["revision_id"] != eligible_revision
                ):
                    build["status"] = "failed"
                    build["claim_owner"] = None
                    build["claim_expires_at"] = None
                    build["failure_reason"] = "superseded by a newer eligible revision"
                    count += 1
            return f"UPDATE {count}"

        if "revision is no longer eligible for an image build" in query:
            count = 0
            for build in self.builds.values():
                if build["status"] == "queued" and not self._build_eligible(build):
                    build["status"] = "failed"
                    build["failure_reason"] = (
                        "revision is no longer eligible for an image build"
                    )
                    count += 1
            return f"UPDATE {count}"

        if "insert into revision_image_builds" in query:
            (
                build_id,
                revision_id,
                generation,
                source_commit,
                source_snapshot_path,
                source_content_digest,
                local_tag,
                base_image_id,
                retry_of_build_id,
                initial_log,
            ) = args
            self.builds[build_id] = {
                "id": build_id,
                "revision_id": revision_id,
                "generation": generation,
                "source_commit": source_commit,
                "source_snapshot_path": source_snapshot_path,
                "source_content_digest": source_content_digest,
                "local_tag": local_tag,
                "base_image_id": base_image_id,
                "status": "queued",
                "attempt": 0,
                "available_at": self.now,
                "queued_at": self.now,
                "claim_owner": None,
                "claim_expires_at": None,
                "build_log": initial_log,
                "failure_reason": None,
                "retry_of_build_id": retry_of_build_id,
                "image_id": None,
                "image_digest": None,
            }
            return "INSERT 0 1"

        if "where revision_id = $1 and status in ('queued', 'building')" in query:
            revision_id, reason = args
            count = 0
            for build in self.builds.values():
                if build["revision_id"] == revision_id and build["status"] in {
                    "queued",
                    "building",
                }:
                    build["status"] = "failed"
                    build["claim_owner"] = None
                    build["claim_expires_at"] = None
                    build["failure_reason"] = reason
                    count += 1
            return f"UPDATE {count}"

        if "id <> $2 and status = 'ready'" in query:
            revision_id, current_build_id = args
            count = 0
            for build in self.builds.values():
                if (
                    build["revision_id"] == revision_id
                    and build["id"] != current_build_id
                    and build["status"] == "ready"
                ):
                    build["status"] = "superseded"
                    count += 1
            return f"UPDATE {count}"

        raise AssertionError(f"unhandled execute query: {query}")

    async def fetch(self, sql, *args):
        query = " ".join(sql.lower().split())
        if "select r.id as revision_id" not in query:
            raise AssertionError(f"unhandled fetch query: {query}")

        revisions = [
            (revision_id, revision)
            for revision_id, revision in self.revisions.items()
            if self._revision_eligible(revision_id)
        ]
        revisions.sort(key=lambda item: (item[1]["created_at"], item[0]))
        rows = []
        rebuild_all = "as retry_of_build_id" in query
        for revision_id, revision in revisions:
            related = [
                build
                for build in self.builds.values()
                if build["revision_id"] == revision_id
            ]
            if not rebuild_all and related:
                continue
            row = {
                "revision_id": revision_id,
                "module_id": revision["module_id"],
                "source_commit": revision["source_commit"],
                "source_snapshot_path": revision["source_snapshot_path"],
                "source_content_digest": revision["source_content_digest"],
            }
            if rebuild_all:
                latest = max(
                    related, key=lambda build: build["generation"], default=None
                )
                active = max(
                    (
                        build
                        for build in related
                        if build["status"] in {"queued", "building"}
                    ),
                    key=lambda build: build["generation"],
                    default=None,
                )
                row["retry_of_build_id"] = latest["id"] if latest else None
                row["active_build_id"] = active["id"] if active else None
                row["active_build_log"] = active["build_log"] if active else None
            rows.append(row)
        return rows

    async def fetchrow(self, sql, *args):
        query = " ".join(sql.lower().split())

        if "with candidate as" in query:
            if any(build["status"] == "building" for build in self.builds.values()):
                return None
            candidates = [
                build
                for build in self.builds.values()
                if build["status"] == "queued"
                and build["available_at"] <= self.now
                and self._build_eligible(build)
            ]
            if not candidates:
                return None

            def priority(build):
                revision = self.revisions[build["revision_id"]]
                serving = (
                    revision["module_id"] in self.endpoint_modules
                    or build["revision_id"] in self.deployment_revisions
                )
                return (
                    0 if serving else 1,
                    build["available_at"],
                    build["queued_at"],
                    build["id"],
                )

            build = min(candidates, key=priority)
            build["status"] = "building"
            build["attempt"] += 1
            build["claim_owner"] = args[0]
            build["claim_expires_at"] = self.now + args[1]
            return {
                **build,
                "module_id": self.revisions[build["revision_id"]]["module_id"],
            }

        if "for update of b, r" in query:
            build = self.builds.get(args[0])
            if build is None:
                return None
            revision = self.revisions[build["revision_id"]]
            return {
                **build,
                "module_id": revision["module_id"],
                "eligible": self._revision_eligible(build["revision_id"]),
            }

        raise AssertionError(f"unhandled fetchrow query: {query}")

    async def fetchval(self, sql, *args):
        query = " ".join(sql.lower().split())

        if "select base_image_id from deployer_base_image_state" in query:
            return self.deployer_base_image_id
        if "where retry_of_build_id = $1 and base_image_id = $2" in query:
            matches = [
                build
                for build in self.builds.values()
                if build["retry_of_build_id"] == args[0]
                and build["base_image_id"] == args[1]
            ]
            matches.sort(key=lambda build: build["generation"], reverse=True)
            return matches[0]["id"] if matches else None

        if "select coalesce(max(generation), 0) + 1" in query:
            generations = [
                build["generation"]
                for build in self.builds.values()
                if build["revision_id"] == args[0]
            ]
            return max(generations, default=0) + 1

        if "with completion_candidate as" in query:
            (
                build_id,
                owner,
                attempt,
                result_status,
                image_id,
                image_digest,
                log,
                reason,
            ) = args
            build = self.builds.get(build_id)
            if (
                build is None
                or build["status"] != "building"
                or build["claim_owner"] != owner
                or build["attempt"] != attempt
            ):
                return None
            eligible = self._build_eligible(build)
            final_status = (
                "failed" if result_status == "ready" and not eligible else result_status
            )
            build["status"] = final_status
            build["image_id"] = image_id if final_status == "ready" else None
            build["image_digest"] = image_digest if final_status == "ready" else None
            build["build_log"] = log
            build["failure_reason"] = (
                "revision eligibility was revoked before image publication"
                if result_status == "ready" and not eligible
                else reason
            )
            build["claim_owner"] = None
            build["claim_expires_at"] = None
            return final_status

        raise AssertionError(f"unhandled fetchval query: {query}")

    def _revision_eligible(self, revision_id: str) -> bool:
        revision = self.revisions[revision_id]
        module = self.modules[revision["module_id"]]
        runtime = self.runtime.get(revision["module_id"])
        return bool(
            module["deleted_at"] is None
            and module["sync_status"] == "synced"
            and module["current_revision_id"] == revision_id
            and runtime is not None
            and runtime["validation_status"] == "passed"
            and runtime["validation_revision_id"] == revision_id
            and revision["source_snapshot_path"] is not None
            and revision["source_content_digest"] is not None
        )

    def _build_eligible(self, build: dict) -> bool:
        if not self._revision_eligible(build["revision_id"]):
            return False
        revision = self.revisions[build["revision_id"]]
        return bool(
            revision["source_snapshot_path"] == build["source_snapshot_path"]
            and revision["source_content_digest"] == build["source_content_digest"]
            and (revision["source_commit"] or "unversioned")
            == (build["source_commit"] or "unversioned")
        )


def _store(
    connection: _StatefulPostgresConnection,
    *,
    base_image_id: str | None = BASE_IMAGE_ID,
) -> PostgresRevisionImageBuildStore:
    store = PostgresRevisionImageBuildStore(
        postgres_dsn="postgresql://unused",
        instance_id="deployer-a",
        deployment_id="compose-project-a",
        base_image_id=base_image_id,
        image_repository="dspy-trainer-module",
        platform_version="2026.09",
        build_log_max_bytes=256,
        leader_timeout_seconds=5,
    )
    store._connection = connection
    return store


def _snapshot(tmp_path: Path, name: str) -> tuple[str, str]:
    path = tmp_path / name
    path.mkdir()
    (path / "bundle.toml").write_text(
        "\n".join(
            (
                f'name = "{name}"',
                'version = "1.0.0"',
                "score_pass_threshold = 0.5",
                "",
                "[runtime]",
                "system_dependency_commands = []",
            )
        ),
        encoding="utf-8",
    )
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "metric.py").write_text("VALUE = 1\n", encoding="utf-8")
    return str(path), calculate_source_content_digest(path)


def _add_revision(
    connection: _StatefulPostgresConnection,
    *,
    module_id: str,
    revision_id: str,
    snapshot: tuple[str, str],
    created_at: float,
    current: bool = True,
    validation_status: str = "passed",
) -> None:
    path, digest = snapshot
    connection.modules.setdefault(
        module_id,
        {"deleted_at": None, "sync_status": "synced", "current_revision_id": None},
    )
    if current:
        connection.modules[module_id]["current_revision_id"] = revision_id
        connection.runtime[module_id] = {
            "validation_status": validation_status,
            "validation_revision_id": revision_id,
        }
    connection.revisions[revision_id] = {
        "module_id": module_id,
        "source_commit": f"commit-{revision_id}",
        "source_snapshot_path": path,
        "source_content_digest": digest,
        "created_at": created_at,
    }


def _add_build(
    connection: _StatefulPostgresConnection,
    *,
    build_id: str,
    revision_id: str,
    status: str = "queued",
    generation: int = 1,
    available_at: float = 0,
    queued_at: float = 0,
    source_content_digest: str | None = None,
    attempt: int = 0,
    claim_owner: str | None = None,
    claim_expires_at: float | None = None,
) -> None:
    revision = connection.revisions[revision_id]
    connection.builds[build_id] = {
        "id": build_id,
        "revision_id": revision_id,
        "generation": generation,
        "source_commit": revision["source_commit"],
        "source_snapshot_path": revision["source_snapshot_path"],
        "source_content_digest": source_content_digest
        or revision["source_content_digest"],
        "local_tag": f"dspy-trainer-module:{revision_id}-g{generation}",
        "base_image_id": BASE_IMAGE_ID,
        "status": status,
        "attempt": attempt,
        "available_at": available_at,
        "queued_at": queued_at,
        "claim_owner": claim_owner,
        "claim_expires_at": claim_expires_at,
        "build_log": "",
        "failure_reason": None,
        "retry_of_build_id": None,
        "image_id": IMAGE_ID if status == "ready" else None,
        "image_digest": None,
    }


def _ready_result() -> RevisionImageBuildResult:
    return RevisionImageBuildResult(
        status="ready",
        local_tag="dspy-trainer-module:ready",
        build_digest=f"sha256:{'c' * 64}",
        dependency_digest=f"sha256:{'d' * 64}",
        image_id=IMAGE_ID,
        image_digest=None,
        labels={"managed": "true"},
        build_log="verified\n",
        failure_reason=None,
    )


def test_reconcile_backfills_current_revision_and_rejects_stale_rows(tmp_path):
    connection = _StatefulPostgresConnection()
    snapshot_old = _snapshot(tmp_path, "old")
    snapshot_current = _snapshot(tmp_path, "current")
    snapshot_invalid = _snapshot(tmp_path, "invalid")
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-old",
        snapshot=snapshot_old,
        created_at=1,
    )
    _add_build(connection, build_id="build-old", revision_id="revision-old")
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-current",
        snapshot=snapshot_current,
        created_at=2,
    )
    _add_revision(
        connection,
        module_id="module-invalid",
        revision_id="revision-invalid",
        snapshot=snapshot_invalid,
        created_at=3,
        validation_status="failed",
    )
    _add_build(connection, build_id="build-invalid", revision_id="revision-invalid")

    inserted = asyncio.run(_store(connection).reconcile_eligible_revisions())

    assert inserted == 1
    assert connection.builds["build-old"]["status"] == "failed"
    assert "superseded" in connection.builds["build-old"]["failure_reason"]
    assert connection.builds["build-invalid"]["status"] == "failed"
    assert "no longer eligible" in connection.builds["build-invalid"]["failure_reason"]
    new_builds = [
        build
        for build in connection.builds.values()
        if build["revision_id"] == "revision-current"
    ]
    assert len(new_builds) == 1
    assert new_builds[0]["status"] == "queued"
    assert new_builds[0]["generation"] == 1
    assert new_builds[0]["source_snapshot_path"] == snapshot_current[0]


def test_claim_prefers_serving_work_then_fifo_and_excludes_ineligible_rows(tmp_path):
    connection = _StatefulPostgresConnection()
    for index, module_id in enumerate(("normal-a", "normal-b", "serving", "invalid")):
        revision_id = f"revision-{module_id}"
        _add_revision(
            connection,
            module_id=module_id,
            revision_id=revision_id,
            snapshot=_snapshot(tmp_path, module_id),
            created_at=index,
        )
    _add_build(
        connection,
        build_id="normal-oldest",
        revision_id="revision-normal-a",
        available_at=1,
        queued_at=1,
    )
    _add_build(
        connection,
        build_id="normal-next",
        revision_id="revision-normal-b",
        available_at=1,
        queued_at=2,
    )
    _add_build(
        connection,
        build_id="serving-later",
        revision_id="revision-serving",
        available_at=20,
        queued_at=20,
    )
    _add_build(
        connection,
        build_id="invalid-earliest",
        revision_id="revision-invalid",
        available_at=0,
        queued_at=0,
        source_content_digest=f"sha256:{'f' * 64}",
    )
    connection.endpoint_modules.add("serving")
    store = _store(connection)

    first = asyncio.run(store.claim_next(claim_timeout_seconds=30))
    assert first is not None and first.build_id == "serving-later"
    connection.builds[first.build_id]["status"] = "ready"

    second = asyncio.run(store.claim_next(claim_timeout_seconds=30))
    assert second is not None and second.build_id == "normal-oldest"
    connection.builds[second.build_id]["status"] = "ready"

    third = asyncio.run(store.claim_next(claim_timeout_seconds=30))
    assert third is not None and third.build_id == "normal-next"
    connection.builds[third.build_id]["status"] = "ready"

    assert asyncio.run(store.claim_next(claim_timeout_seconds=30)) is None
    assert connection.builds["invalid-earliest"]["status"] == "queued"


def test_expired_claim_is_requeued_without_losing_attempt_history(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    _add_build(
        connection,
        build_id="expired",
        revision_id="revision-a",
        status="building",
        attempt=2,
        claim_owner="dead-deployer",
        claim_expires_at=99,
    )

    recovered = asyncio.run(_store(connection).recover_expired_claims())

    build = connection.builds["expired"]
    assert recovered == 1
    assert build["status"] == "queued"
    assert build["attempt"] == 2
    assert build["claim_owner"] is None
    assert build["claim_expires_at"] is None
    assert "claim expired" in build["build_log"]


def test_manual_retry_creates_next_generation_with_provenance(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    _add_build(
        connection,
        build_id="failed-build",
        revision_id="revision-a",
        status="failed",
        generation=3,
    )
    store = _store(connection)

    retry_id = asyncio.run(store.enqueue_retry("failed-build"))

    retry = connection.builds[retry_id]
    assert retry["status"] == "queued"
    assert retry["generation"] == 4
    assert retry["retry_of_build_id"] == "failed-build"
    assert connection.builds["failed-build"]["status"] == "failed"


def test_rebuild_all_supersedes_active_work_and_advances_each_generation(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    _add_revision(
        connection,
        module_id="module-b",
        revision_id="revision-b",
        snapshot=_snapshot(tmp_path, "revision-b"),
        created_at=2,
    )
    _add_build(
        connection,
        build_id="ready-a",
        revision_id="revision-a",
        status="ready",
        generation=2,
    )
    _add_build(
        connection,
        build_id="failed-b",
        revision_id="revision-b",
        status="failed",
        generation=1,
    )
    store = _store(connection)

    rebuild_ids = asyncio.run(store.enqueue_rebuild_all())

    assert len(rebuild_ids) == 2
    rebuild_a, rebuild_b = (connection.builds[build_id] for build_id in rebuild_ids)
    assert (rebuild_a["revision_id"], rebuild_a["generation"]) == (
        "revision-a",
        3,
    )
    assert rebuild_a["retry_of_build_id"] == "ready-a"
    assert (rebuild_b["revision_id"], rebuild_b["generation"]) == (
        "revision-b",
        2,
    )
    assert rebuild_b["retry_of_build_id"] == "failed-b"
    assert connection.builds["failed-b"]["status"] == "failed"
    assert connection.builds["failed-b"]["failure_reason"] is None


def test_completion_atomically_fences_revoked_eligibility_and_never_publishes(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    _add_build(
        connection,
        build_id="building-a",
        revision_id="revision-a",
        status="building",
        attempt=1,
        claim_owner="deployer-a",
        claim_expires_at=130,
    )
    store = _store(connection)
    claim = asyncio.run(store.claim_next(claim_timeout_seconds=30))
    assert claim is None
    build = connection.builds["building-a"]

    owned_claim = RevisionImageBuildClaim(
        build_id="building-a",
        module_id="module-a",
        revision_id="revision-a",
        generation=1,
        source_commit=build["source_commit"],
        source_snapshot_path=Path(build["source_snapshot_path"]),
        source_content_digest=build["source_content_digest"],
        local_tag=build["local_tag"],
        base_image_id=BASE_IMAGE_ID,
        attempt=1,
        claim_owner="deployer-a",
    )
    connection.runtime["module-a"]["validation_status"] = "failed"

    published = asyncio.run(store.complete_claim(owned_claim, _ready_result()))

    assert not published
    assert build["status"] == "failed"
    assert build["image_id"] is None
    assert build["claim_owner"] is None
    assert build["failure_reason"] == (
        "revision eligibility was revoked before image publication"
    )


def test_retry_rejects_active_ineligible_and_duplicate_generations(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    _add_build(
        connection,
        build_id="active-build",
        revision_id="revision-a",
        status="queued",
    )
    store = _store(connection)

    with pytest.raises(RevisionImageEnqueueError) as active_error:
        asyncio.run(store.enqueue_retry("active-build"))
    assert active_error.value.code == "build_conflict"

    connection.builds["active-build"]["status"] = "failed"
    connection.modules["module-a"]["sync_status"] = "syncing"
    with pytest.raises(RevisionImageEnqueueError) as ineligible_error:
        asyncio.run(store.enqueue_retry("active-build"))
    assert ineligible_error.value.code == "not_eligible"

    connection.modules["module-a"]["sync_status"] = "synced"
    asyncio.run(store.enqueue_retry("active-build"))
    with pytest.raises(RevisionImageEnqueueError) as duplicate_error:
        asyncio.run(store.enqueue_retry("active-build"))
    assert duplicate_error.value.code == "build_conflict"


def test_rebuild_all_rejects_duplicate_active_request(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    store = _store(connection)

    first = asyncio.run(store.enqueue_rebuild_all())
    assert len(first) == 1
    with pytest.raises(RevisionImageEnqueueError) as duplicate_error:
        asyncio.run(store.enqueue_rebuild_all())
    assert duplicate_error.value.code == "build_conflict"


def test_concurrent_rebuild_all_requests_create_one_generation(tmp_path):
    connection = _StatefulPostgresConnection()
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    store = _store(connection)

    async def run_concurrently():
        return await asyncio.gather(
            store.enqueue_rebuild_all(),
            store.enqueue_rebuild_all(),
            return_exceptions=True,
        )

    results = asyncio.run(run_concurrently())
    successes = [result for result in results if isinstance(result, list)]
    conflicts = [
        result for result in results if isinstance(result, RevisionImageEnqueueError)
    ]

    assert len(successes) == 1
    assert len(successes[0]) == 1
    assert len(conflicts) == 1
    assert conflicts[0].code == "build_conflict"
    assert len(connection.builds) == 1


def test_backend_enqueue_uses_registered_immutable_base_image(tmp_path):
    connection = _StatefulPostgresConnection()
    connection.deployer_base_image_id = IMAGE_ID
    _add_revision(
        connection,
        module_id="module-a",
        revision_id="revision-a",
        snapshot=_snapshot(tmp_path, "revision-a"),
        created_at=1,
    )
    store = _store(connection, base_image_id=None)

    build_ids = asyncio.run(store.enqueue_rebuild_all())

    assert len(build_ids) == 1
    assert connection.builds[build_ids[0]]["base_image_id"] == IMAGE_ID
